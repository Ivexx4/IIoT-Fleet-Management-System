import sys
import time
import argparse
from typing import Any

import paho.mqtt.client as mqtt
from datetime import datetime, timezone

from models import RobotTelemetry, StationTelemetry
from mqtt_config import MQTT_BROKER, MQTT_PORT, MQTT_QOS, create_mqtt_client

# --- CONFIGURAÇÕES DE SIMULAÇÃO ---
PROCESSING_TIME = 5.0  # Tempo em segundos para simular o embalamento/carregamento


class LoadingStation:
    """
    Simula uma Estação de Embalamento/Carregamento (P0, P1, etc.)
    """

    def __init__(
        self,
        group_id: str,
        station_id: str,
        broker_address: str = MQTT_BROKER,
        broker_port: int = MQTT_PORT,
    ) -> None:
        self.group_id = group_id
        self.station_id = station_id  # Ex: 'P0', 'P1'
        self.broker_address = broker_address
        self.broker_port = broker_port

        self.status = "IDLE"
        self.current_delivery: dict[str, Any] | None = None
        self.state_start_time = time.time()

        client_name = f"{group_id}_{station_id}"
        self.client = create_mqtt_client(client_name)

        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

        # Tópicos
        # Publica o estado da estação (útil para monitorização e Fleet Coordinator)
        self.topic_pub = f"warehouse/{group_id}/station/{station_id}/status"
        # Subscreve aos estados dos robôs para saber quando uma entrega chega
        self.topic_sub_amr_status = f"warehouse/{group_id}/amr/+/status"

    def on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: dict[str, Any],
        reason_code: Any,
        properties: Any,
    ) -> None:
        if not reason_code.is_failure:
            print(f"✅ Ligado ao Broker MQTT ({self.broker_address}) como {self.station_id}!")
            # 📡 Escuta por qualquer atualização de estado de robô
            client.subscribe(self.topic_sub_amr_status, qos=MQTT_QOS)
            print(f"📡 A escutar: {self.topic_sub_amr_status}")
            self.publish_status()
        else:
            print(f"❌ Falha na ligação, código de retorno: {reason_code}")

    def on_message(
        self,
        client: mqtt.Client,
        userdata: Any,
        msg: mqtt.MQTTMessage,
    ) -> None:
        """
        Processa as mensagens recebidas, principalmente as atualizações de estado dos robôs.
        """
        try:
            payload = RobotTelemetry.model_validate_json(msg.payload)
        except ValueError as error:
            print(f"[{self.station_id}] Rejected robot telemetry: {error}")
            return

        # O ID da localização que o robô reporta (ex: "STATION-P1")
        robot_loc = payload.location_id
        # O ID esperado (ex: "STATION-P1")
        my_loc_tag = f"STATION-{self.station_id}"

        # 1. Checa se o robô está a fazer uma entrega na MINHA estação
        if (msg.topic.endswith("/status") and
                (robot_loc == self.station_id or robot_loc == my_loc_tag) and # <--- CORREÇÃO AQUI
                payload.status == "DROPPING" and  # O robô está a descarregar
                self.status == "IDLE"):  # A estação está livre

            # Uma entrega está a chegar!
            print(f"\n📦 Entrega detetada de {payload.robot_id} em {self.station_id}. A iniciar processamento...")
            self.current_delivery = payload.model_dump()
            self.change_state("PROCESSING")

    def change_state(self, new_status: str) -> None:
        """Atualiza o estado e publica a alteração."""
        if self.status != new_status:
            self.status = new_status
            self.state_start_time = time.time()
            print(f"[{self.station_id}] -> Estado: {self.status}")
            self.publish_status()

    def update_logic(self) -> None:
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

    def publish_status(self) -> None:
        """Publica o estado atual da estação via MQTT."""
        # Se estiver a processar, indica o tempo restante
        print("A Publicar:")
        time_remaining = 0
        if self.status == "PROCESSING":
            time_remaining = max(0.0, PROCESSING_TIME - (time.time() - self.state_start_time))

        payload = StationTelemetry(
            station_id=self.station_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            status=self.status,
            delivery_info=self.current_delivery,
            time_remaining_s=round(time_remaining, 1),
        )
        self.client.publish(
            self.topic_pub,
            payload.model_dump_json(),
            qos=MQTT_QOS,
            retain=True,
        )

    def run(self) -> None:
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