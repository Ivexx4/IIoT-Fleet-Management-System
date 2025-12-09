import sys
import time
import json
import argparse
import paho.mqtt.client as mqtt
from datetime import datetime, timezone
from mqqtt_config import MQTT_BROKER, MQTT_PORT

# --- CONFIGURAÇÕES DE SIMULAÇÃO ---
PROCESSING_TIME = 5.0  # Tempo em segundos para simular o embalamento/carregamento


class LoadingStation:
    """
    Simula uma Estação de Embalamento/Carregamento (P0, P1, etc.)
    """

    def __init__(self, group_id, station_id, broker_address, broker_port=MQTT_PORT):
        self.group_id = group_id
        self.station_id = station_id  # Ex: 'P0', 'P1'
        self.broker_address = broker_address
        self.broker_port = broker_port

        self.status = "IDLE"
        self.current_delivery = None  # Informação sobre a entrega a processar
        self.state_start_time = time.time()

        client_name = f"{group_id}_{station_id}"
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_name
        )

        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

        # Tópicos
        # Publica o estado da estação (útil para monitorização e Fleet Coordinator)
        self.topic_pub = f"warehouse/{group_id}/station/{station_id}/status"
        # Subscreve aos estados dos robôs para saber quando uma entrega chega
        self.topic_sub_amr_status = f"warehouse/{group_id}/amr/+/status"

    def on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            print(f"✅ Ligado ao Broker MQTT ({self.broker_address}) como {self.station_id}!")
            # 📡 Escuta por qualquer atualização de estado de robô
            client.subscribe(self.topic_sub_amr_status)
            print(f"📡 A escutar: {self.topic_sub_amr_status}")
            self.publish_status()
        else:
            print(f"❌ Falha na ligação, código de retorno: {reason_code}")

    def on_message(self, client, userdata, msg):
        """
        Processa as mensagens recebidas, principalmente as atualizações de estado dos robôs.
        """
        try:
            payload = json.loads(msg.payload.decode('utf-8'))
        except json.JSONDecodeError:
            return  # Ignora mensagens não-JSON

        # O ID da localização que o robô reporta (ex: "STATION-P1")
        robot_loc = payload.get("location_id", "")
        # O ID esperado (ex: "STATION-P1")
        my_loc_tag = f"STATION-{self.station_id}"

        # 1. Checa se o robô está a fazer uma entrega na MINHA estação
        if (msg.topic.endswith("/status") and
                (robot_loc == self.station_id or robot_loc == my_loc_tag) and # <--- CORREÇÃO AQUI
                payload.get("status") == "DROPPING" and  # O robô está a descarregar
                self.status == "IDLE"):  # A estação está livre

            # Uma entrega está a chegar!
            print(f"\n📦 Entrega detetada de {payload['robot_id']} em {self.station_id}. A iniciar processamento...")
            self.current_delivery = payload
            self.change_state("PROCESSING")

    def change_state(self, new_status):
        """Atualiza o estado e publica a alteração."""
        if self.status != new_status:
            self.status = new_status
            self.state_start_time = time.time()
            print(f"[{self.station_id}] -> Estado: {self.status}")
            self.publish_status()

    def update_logic(self):
        """Contém a lógica de tempo da estação."""
        elapsed_in_state = time.time() - self.state_start_time

        if self.status == "PROCESSING":
            if elapsed_in_state >= PROCESSING_TIME:
                print(f"📦 Pedido concluído e embalado! Estação {self.station_id} agora livre.")
                self.current_delivery = None
                self.change_state("IDLE")

        # Em IDLE, não faz nada, apenas espera por nova entrega (lida no on_message)
        elif self.status == "IDLE":
            pass

        self.publish_status()

    def publish_status(self):
        """Publica o estado atual da estação via MQTT."""
        # Se estiver a processar, indica o tempo restante
        print("A Publicar:")
        time_remaining = 0
        if self.status == "PROCESSING":
            time_remaining = max(0.0, PROCESSING_TIME - (time.time() - self.state_start_time))

        payload = {
            "station_id": self.station_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": self.status,
            "delivery_info": self.current_delivery,
            "time_remaining_s": round(time_remaining, 1)
        }
        print(payload)
        self.client.publish(self.topic_pub, json.dumps(payload))

    def run(self):
        """Inicia a simulação."""
        self.client.connect(self.broker_address, self.broker_port, 60)
        self.client.loop_start()  # Corre o loop MQTT numa thread separada

        try:
            while True:
                self.update_logic()
                time.sleep(1.0)  # Ciclo de atualização de 1 segundo
        except KeyboardInterrupt:
            print(f"\n🛑 A parar a Estação {self.station_id}...")
            self.client.loop_stop()
            self.client.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Simulador de Estação de Embalamento/Carregamento")
    parser.add_argument("group_id")
    parser.add_argument("station_id")
    args = parser.parse_args()

    # É importante usar o GROUP_ID definido
    station = LoadingStation(args.group_id, args.station_id, MQTT_BROKER, MQTT_PORT)
    station.run()