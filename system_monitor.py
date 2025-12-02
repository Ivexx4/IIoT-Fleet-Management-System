import json
import socket
import time
import threading
import paho.mqtt.client as mqtt
from datetime import datetime

# --- CONFIGURAÇÃO ---
GROUP_ID = "2024209219"  # Teu ID
MQTT_BROKER = "srsa-pi-8.dei.uc.pt"
# MQTT_BROKER = "localhost"
MQTT_PORT = 1883

# Configuração UDP (Envia alertas para o Gateway)
GATEWAY_IP = "127.0.0.1"  # O Gateway está a correr localmente
GATEWAY_PORT = 9090  # Porta definida no enunciado [cite: 175]

# Tópico para escutar dados "limpos" do Gateway
TOPIC_INTERNAL_AMR = f"{GROUP_ID}/internal/amr/+/status"

# Parâmetros de Saúde
BATTERY_THRESHOLD = 15.0  # Crítico abaixo de 15%
STALL_TIMEOUT = 30.0  # Segundos sem mudar de sítio enquanto se move
ALERT_COOLDOWN = 10.0  # Evitar spam de alertas (segundos)


class SystemMonitor:
    def __init__(self):
        # Estado Local da Frota
        # Estrutura: { 'AMR-1': { 'data': {...}, 'last_move_time': float, 'last_alert': float } }
        self.robots = {}

        # MQTT Client
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

        # Socket UDP (Cliente)
        self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def start(self):
        print(f"--- System Monitor ({GROUP_ID}) Started ---")

        # 1. Iniciar MQTT
        try:
            self.client.connect(MQTT_BROKER, MQTT_PORT, 60)
            self.client.loop_start()
        except Exception as e:
            print(f"MQTT Connection Error: {e}")

        # 2. Loop de Monitorização (Health Check)
        try:
            while True:
                self.check_fleet_health()
                time.sleep(1)  # Verifica a cada segundo
        except KeyboardInterrupt:
            self.client.loop_stop()
            self.udp_sock.close()
            print("System Monitor Stopped.")

    # --- MQTT HANDLING ---
    def on_connect(self, client, userdata, flags, rc, props):
        if rc == 0:
            print(f"Connected to MQTT. Listening to {TOPIC_INTERNAL_AMR}")
            client.subscribe(TOPIC_INTERNAL_AMR)

    def on_message(self, client, userdata, msg):
        """Atualiza o estado local do robô sempre que recebe dados do Gateway"""
        try:
            payload = json.loads(msg.payload.decode())
            robot_id = payload.get("robot_id")

            if not robot_id: return

            current_time = time.time()
            new_location = payload.get("location_id")

            # Inicializa o registo se for novo
            if robot_id not in self.robots:
                self.robots[robot_id] = {
                    "data": payload,
                    "last_move_time": current_time,
                    "last_alert": 0
                }
            else:
                # Lógica para detetar movimento (para o check de STALL)
                old_location = self.robots[robot_id]["data"].get("location_id")

                # Se a localização mudou, reseta o temporizador de movimento
                if new_location != old_location:
                    self.robots[robot_id]["last_move_time"] = current_time

                # Atualiza os dados mais recentes
                self.robots[robot_id]["data"] = payload

        except Exception as e:
            print(f"Error parsing MQTT: {e}")

    # --- HEALTH CHECKS ---
    def check_fleet_health(self):
        """Analisa o estado de cada robô e envia alertas UDP se necessário"""
        now = time.time()

        for r_id, record in self.robots.items():
            data = record["data"]
            battery = float(data.get("battery", 100))
            status = data.get("status", "IDLE")
            last_move = record["last_move_time"]
            last_alert = record["last_alert"]

            # Verificar Cooldown (não enviar alertas repetidos a cada milissegundo)
            if (now - last_alert) < ALERT_COOLDOWN:
                continue

            alert_msg = None

            # 1. CHECK: LOW BATTERY [cite: 224]
            # Se bateria < 15% E não está a carregar
            if battery < BATTERY_THRESHOLD and status not in ["CHARGING", "MOVING_TO_CHARGE"]:
                alert_msg = {
                    "robot_id": r_id,
                    "level": "CRITICAL",
                    "override_task": "FORCE_CHARGE",  # Comando que o Gateway entende
                    "message": f"Battery Critical ({battery}%)"
                }
                print(f"[HEALTH WARNING] {r_id} Low Battery!")

            # 2. CHECK: STALLED [cite: 222, 223]
            # Se status diz "MOVING..." mas a localização não muda há 30s
            elif "MOVING" in status and (now - last_move) > STALL_TIMEOUT:
                alert_msg = {
                    "robot_id": r_id,
                    "level": "CRITICAL",
                    "override_task": "FORCE_CHARGE",  # Forçar recolha/reset
                    "message": f"Robot Stalled in {data.get('location_id')} for {int(now - last_move)}s"
                }
                print(f"[HEALTH WARNING] {r_id} seems STALLED!")

            # Enviar Alerta via UDP se detetado problema
            if alert_msg:
                self.send_udp_alert(alert_msg)
                self.robots[r_id]["last_alert"] = now

    def send_udp_alert(self, alert_json):
        """Envia JSON via UDP para o Warehouse Gateway"""
        try:
            message = json.dumps(alert_json).encode('utf-8')
            self.udp_sock.sendto(message, (GATEWAY_IP, GATEWAY_PORT))
            print(f"   -> Sent UDP Alert to Gateway: {alert_json['override_task']}")
        except Exception as e:
            print(f"UDP Send Error: {e}")


if __name__ == "__main__":
    monitor = SystemMonitor()
    monitor.start()