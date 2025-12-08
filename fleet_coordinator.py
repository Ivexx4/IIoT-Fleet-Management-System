import json
import socket
import time
import threading
import paho.mqtt.client as mqtt
from mqqtt_config import MQTT_BROKER,MQTT_PORT,GROUP_ID
UDP_IP = "0.0.0.0"

# Porta para receber encomendas do cliente
UDP_PORT_ORDERS = 9091  # Porta diferente do Monitor (9090)!

# Tópicos
TOPIC_INTERNAL_SUB = f"{GROUP_ID}/internal/#"  # Escuta estados limpos do Gateway
TOPIC_DISPATCH_PUB = f"{GROUP_ID}/internal/tasks/dispatch"  # Envia ordens para o Gateway


class FleetCoordinator:
    def __init__(self):
        # Estado do Mundo (Dicionários para guardar o último estado conhecido)
        self.robots = {}  # Ex: {'AMR-1': {'status': 'IDLE', 'battery': 100}}
        self.shelves = {}  # Ex: {'S1': {'item': 'item_A', 'stock': 100}}
        self.pending_orders = []  # Fila de encomendas

        # MQTT
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

        # UDP Server
        self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp_sock.bind((UDP_IP, UDP_PORT_ORDERS))

    def start(self):
        print(f"--- Fleet Coordinator ({GROUP_ID}) Started ---")
        # 1. Iniciar MQTT
        try:
            self.client.connect(MQTT_BROKER, MQTT_PORT, 60)
            self.client.loop_start()
        except Exception as e:
            print(f"MQTT Connection Error: {e}")

        # 2. Iniciar Thread UDP (Receber Encomendas)
        udp_thread = threading.Thread(target=self.run_udp_server)
        udp_thread.daemon = True
        udp_thread.start()

        # 3. Loop Principal (Processamento de Lógica)
        try:
            while True:
                self.process_logic()
                time.sleep(1)
        except KeyboardInterrupt:
            self.client.loop_stop()
            self.udp_sock.close()
            print("Coordinator Stopped.")

    # --- MQTT HANDLING ---
    def on_connect(self, client, userdata, flags, rc, props):
        if rc == 0:
            print(f"Connected to MQTT. Subscribing to {TOPIC_INTERNAL_SUB}")
            client.subscribe(TOPIC_INTERNAL_SUB)

    def on_message(self, client, userdata, msg):
        """Atualiza o 'World State' com base nos dados limpos do Gateway"""
        try:
            topic = msg.topic
            payload = json.loads(msg.payload.decode())
            # Atualizar Estado dos Robôs
            if "/internal/amr/" in topic:
                r_id = payload.get("robot_id")
                if r_id:
                    self.robots[r_id] = payload

            # Atualizar Estado das Prateleiras
            elif "/internal/static/" in topic:
                s_id = payload.get("asset id")
                if s_id:
                    self.shelves[s_id] = payload

        except Exception as e:
            print(f"Error parsing MQTT message: {e}")

    # --- UDP SERVER ---
    def run_udp_server(self):
        """Recebe encomendas do Cliente (JSON)"""
        print(f"Listening for Orders on UDP {UDP_PORT_ORDERS}...")
        while True:
            try:
                data, addr = self.udp_sock.recvfrom(1024)
                order_msg = data.decode('utf-8')
                print(f"[New Order Received]: {order_msg}")

                try:
                    order = json.loads(order_msg)
                    # Adiciona à lista de pendentes
                    self.pending_orders.append(order)
                except json.JSONDecodeError:
                    print("Invalid JSON order received")
            except Exception as e:
                print(f"UDP Error: {e}")

    # --- MAIN LOGIC ---
    def process_logic(self):
        """Tenta atribuir encomendas pendentes a robôs disponíveis"""
        if not self.pending_orders:
            return

        # Para cada encomenda pendente
        # Ex: {"item": "item_A", "quantity": 10, "pack_station": "P1"}
        # Iterar sobre uma cópia para poder remover itens
        for order in self.pending_orders[:]:
            item_needed = order.get("item")
            target_station = order.get("pack_station", "P1")

            # 1. Encontrar Prateleira com Stock
            target_shelf_id = None
            for s_id, data in self.shelves.items():
                # Verifica se a prateleira tem o item e stock suficiente (>0 para simplificar)
                if data.get("item id") == item_needed and data.get("stock_units", 0) > 0:
                    target_shelf_id = s_id
                    break

            if not target_shelf_id:
                # Se não há stock, esperar (não remover da lista)
                # print(f"Waiting for stock of {item_needed}...")
                continue

            # 2. Encontrar Robô IDLE
            target_robot_id = None
            for r_id, data in self.robots.items():
                if data.get("status") == "IDLE" and data.get("battery", 0) > 15:
                    target_robot_id = r_id
                    break

            if target_robot_id:
                # 3. MATCH ENCONTRADO! Despachar Tarefa
                print(f"MATCH! Assigning {target_robot_id} to fetch {item_needed} at {target_shelf_id}")

                dispatch_cmd = {
                    "robot_id": target_robot_id,
                    "command": "EXECUTE_TASK",
                    "target_shelf_id": target_shelf_id,
                    "target_station_id": target_station
                }

                # Publica para o Gateway
                self.client.publish(TOPIC_DISPATCH_PUB, json.dumps(dispatch_cmd))

                # Remove a encomenda da lista
                self.pending_orders.remove(order)


if __name__ == "__main__":
    coord = FleetCoordinator()
    coord.start()