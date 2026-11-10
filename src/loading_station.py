import argparse
import random
import time
from collections import deque
from datetime import UTC, datetime
from typing import Any

import paho.mqtt.client as mqtt

from models import (
    InfrastructureLocation,
    LocationRequest,
    RobotTelemetry,
    StationTelemetry,
)
from mqtt_config import MQTT_BROKER, MQTT_PORT, MQTT_QOS, create_mqtt_client
from task_allocator import location_coordinates

# --- CONFIGURAÇÕES DE SIMULAÇÃO ---
BASE_PROCESSING_TIME = 4.0
PROCESSING_TIME_PER_UNIT = 0.5


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
        self.processing_time = BASE_PROCESSING_TIME
        self.delivery_queue: deque[RobotTelemetry] = deque()
        self.robots_dropping: set[str] = set()
        self.state_start_time = time.monotonic()

        client_name = f"{group_id}_{station_id}"
        self.client = create_mqtt_client(client_name)

        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

        # Tópicos
        # Publica o estado da estação (útil para monitorização e Fleet Coordinator)
        self.topic_pub = f"warehouse/{group_id}/station/{station_id}/status"
        # Subscreve aos estados dos robôs para saber quando uma entrega chega
        self.topic_sub_amr_status = f"warehouse/{group_id}/amr/+/status"
        self.location_request_topic = (
            f"{group_id}/internal/infrastructure/location/request"
        )
        self.location_report_topic = f"{group_id}/internal/infrastructure/location"

    def on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: dict[str, Any],
        reason_code: Any,
        properties: Any,
    ) -> None:
        if not reason_code.is_failure:
            print(
                f"✅ Ligado ao Broker MQTT ({self.broker_address}) "
                f"como {self.station_id}!"
            )
            # 📡 Escuta por qualquer atualização de estado de robô
            client.subscribe(
                [
                    (self.topic_sub_amr_status, MQTT_QOS),
                    (self.location_request_topic, MQTT_QOS),
                ]
            )
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
        """Process location requests and incoming robot status."""
        if msg.topic == self.location_request_topic:
            try:
                LocationRequest.model_validate_json(msg.payload)
                self.publish_location()
            except ValueError as error:
                print(f"[{self.station_id}] Rejected location request: {error}")
            return

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
        if (
            not msg.topic.endswith("/status")
            or robot_loc not in {self.station_id, my_loc_tag}
            or payload.status != "DROPPING"
        ):
            self.robots_dropping.discard(payload.robot_id)
            return
        if payload.robot_id in self.robots_dropping:
            return

        self.robots_dropping.add(payload.robot_id)
        if self.status == "IDLE":
            self.start_delivery(payload)
        else:
            self.delivery_queue.append(payload)

    def start_delivery(self, delivery: RobotTelemetry) -> None:
        print(
            f"\n📦 Entrega de {delivery.robot_id} detetada "
            f"em {self.station_id}; a processar..."
        )
        self.current_delivery = delivery.model_dump()
        self.processing_time = (
            BASE_PROCESSING_TIME
            + delivery.load_quantity * PROCESSING_TIME_PER_UNIT
            + random.uniform(0.0, 1.5)
        )
        self.change_state("PROCESSING")

    def change_state(self, new_status: str) -> None:
        """Atualiza o estado e publica a alteração."""
        if self.status != new_status:
            self.status = new_status
            self.state_start_time = time.monotonic()
            print(f"[{self.station_id}] -> Estado: {self.status}")
            self.publish_status()

    def update_logic(self) -> None:
        """Contém a lógica de tempo da estação."""
        elapsed_in_state = time.monotonic() - self.state_start_time

        if self.status == "PROCESSING":
            if elapsed_in_state >= self.processing_time:
                self.current_delivery = None
                self.change_state("IDLE")
                if self.delivery_queue:
                    self.start_delivery(self.delivery_queue.popleft())

        # Em IDLE, não faz nada, apenas espera por nova entrega (lida no on_message)
        elif self.status == "IDLE":
            pass

        self.publish_status()

    def publish_status(self) -> None:
        """Publica o estado atual da estação via MQTT."""
        x, y = location_coordinates(self.station_id)
        # Se estiver a processar, indica o tempo restante
        time_remaining = 0.0
        if self.status == "PROCESSING":
            time_remaining = max(
                0.0,
                self.processing_time - (time.monotonic() - self.state_start_time),
            )

        payload = StationTelemetry(
            station_id=self.station_id,
            location_id=f"STATION-{self.station_id}",
            x=x,
            y=y,
            timestamp=datetime.now(UTC).isoformat(),
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

    def publish_location(self) -> None:
        x, y = location_coordinates(self.station_id)
        location = InfrastructureLocation(
            asset_type="station",
            asset_id=self.station_id,
            location_id=f"STATION-{self.station_id}",
            x=x,
            y=y,
        )
        result = self.client.publish(
            self.location_report_topic,
            location.model_dump_json(),
            qos=MQTT_QOS,
        )
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            print(f"[{self.station_id}] MQTT rejected location report")

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
    parser = argparse.ArgumentParser(
        description="Simulador de Estação de Embalamento/Carregamento"
    )
    parser.add_argument("group_id")
    parser.add_argument("station_id")
    args = parser.parse_args()

    # É importante usar o GROUP_ID definido
    station = LoadingStation(args.group_id, args.station_id, MQTT_BROKER, MQTT_PORT)
    station.run()
