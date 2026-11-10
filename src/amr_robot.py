import argparse
import json
import random
import socket
import sys
import time
from datetime import UTC, datetime
from typing import Any

import paho.mqtt.client as mqtt

from models import RobotTelemetry
from mqtt_config import GROUP_ID, MQTT_BROKER, MQTT_PORT, MQTT_QOS, create_mqtt_client
from protocol import decode_command

# --- CONFIGURAÇÕES DE SIMULAÇÃO ---
STALL_PROBABILITY = 5  # 5% de probabilidade
BATTERY_DECAY_RATE = 1  # 1% por segundo


class AMRRobot:
    def __init__(
        self,
        group_id: str,
        robot_id: str,
        broker_address: str = MQTT_BROKER,
        broker_port: int = MQTT_PORT,
    ) -> None:
        self.group_id = group_id
        self.robot_id = robot_id
        self.broker_address = broker_address
        self.broker_port = broker_port

        self.status = "IDLE"
        self.location_id = "DOCK"
        self.battery = 100.0

        self.target_shelf: str | None = None
        self.target_station: str | None = None

        self.state_start_time = time.time()

        client_name = f"{group_id}_{robot_id}"
        self.client = create_mqtt_client(client_name)
        self.availability_topic = f"{group_id}/internal/amr/{robot_id}/availability"
        self.client.will_set(
            self.availability_topic,
            json.dumps({"robot_id": robot_id, "status": "offline"}),
            qos=MQTT_QOS,
            retain=True,
        )

        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

        self.topic_pub = f"warehouse/{group_id}/amr/{robot_id}/status"
        self.topic_sub = f"warehouse/{group_id}/amr/{robot_id}/command"

        self.last_battery_update = time.time()

    def connect(self) -> None:
        try:
            print(f"[{self.robot_id}] Connecting to Broker at {self.broker_address}...")
            self.client.connect(self.broker_address, self.broker_port, 60)
            self.client.loop_start()
            self.publish_status()
        except Exception as e:
            print(f"Connection Failed: {e}")
            sys.exit(1)

    def on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: dict[str, Any],
        reason_code: Any,
        properties: Any,
    ) -> None:
        if not reason_code.is_failure:
            print(f"[{self.robot_id}] Connected! Subscribing to {self.topic_sub}")
            client.subscribe(self.topic_sub, qos=MQTT_QOS)
            client.publish(
                self.availability_topic,
                json.dumps({"robot_id": self.robot_id, "status": "online"}),
                qos=MQTT_QOS,
                retain=True,
            )
            self.publish_status()
        else:
            print(f"[{self.robot_id}] Connection Failed with code {reason_code}")

    def on_message(
        self,
        client: mqtt.Client,
        userdata: Any,
        msg: mqtt.MQTTMessage,
    ) -> None:
        try:
            cmd_type, shelf_byte, station_byte = decode_command(msg.payload)
            print(
                f"[{self.robot_id}] RX CMD: Type={cmd_type}, Shelf={shelf_byte}, Station={station_byte}"
            )

            # 0x03: FORCE_CHARGE (Override)
            if cmd_type == 0x03:
                print(f"[{self.robot_id}] !!! OVERRIDE: FORCE CHARGE !!!")
                # Em override forçamos a transição, assumimos que sai do sítio
                self.location_id = "TRANSIT"
                self.change_state("MOVING_TO_CHARGE")
                return

            # 0x01: EXECUTE_TASK
            if self.status == "STALLED":
                print(f"[{self.robot_id}] Ignored Task (STALLED)")
                return

            if cmd_type == 0x01 and self.status == "IDLE":
                self.target_shelf = f"SHELF-S{shelf_byte}"
                self.target_station = f"STATION-P{station_byte}"

                # CORREÇÃO: Não mudamos location_id aqui. Deixamos o change_state decidir.
                self.change_state("MOVING_TO_PICK")

        except ValueError as error:
            print(f"Rejected command for {self.robot_id}: {error}")

    def change_state(self, new_state: str) -> None:
        """
        Gere a transição de estados e verifica falhas (Stall) ANTES de confirmar o movimento.
        """
        # Variável temporária para saber se falhou
        stalled = False

        # Verifica falha APENAS se estivermos a tentar iniciar movimento
        if new_state in ["MOVING_TO_PICK", "MOVING_TO_DROP"]:
            if random.randint(1, 100) <= STALL_PROBABILITY:
                stalled = True
                print(f"[{self.robot_id}] !!! FAILURE ON TRANSITION: STALLED !!!")

        # --- APLICAÇÃO DO ESTADO ---
        if stalled:
            self.status = "STALLED"
            # IMPORTANTE: Se estagnou, NÃO muda location_id para TRANSIT.
            # Mantém a localização anterior (DOCK ou SHELF).
        else:
            self.status = new_state
            # Se o novo estado é de movimento e não falhou, então sim, estamos em trânsito
            if new_state in ["MOVING_TO_PICK", "MOVING_TO_DROP"]:
                self.location_id = "TRANSIT"

        self.state_start_time = time.time()
        print(
            f"[{self.robot_id}] State changed to: {self.status} (Loc: {self.location_id})"
        )
        self.publish_status()

    def update_logic(self) -> None:
        now = time.time()
        elapsed_in_state = now - self.state_start_time

        # Se estiver STALLED, sai imediatamente. Não conta tempo, não muda localização.
        if self.status == "STALLED":
            return

        # --- BATTERY LOGIC ---
        active_states = [
            "MOVING_TO_PICK",
            "PICKING",
            "MOVING_TO_DROP",
            "DROPPING",
            "MOVING_TO_CHARGE",
        ]
        if self.status in active_states:
            if now - self.last_battery_update >= 1.0:
                self.battery -= BATTERY_DECAY_RATE
                self.last_battery_update = now
                if self.battery < 0:
                    self.battery = 0
                self.publish_status()
        else:
            self.last_battery_update = now

        # --- TIMERS ---
        if self.status == "IDLE":
            pass  # Fica quieto no DOCK

        elif self.status == "MOVING_TO_PICK":
            if elapsed_in_state >= 3.0:
                if self.target_shelf is None:
                    return
                self.location_id = self.target_shelf
                self.change_state("PICKING")

        elif self.status == "PICKING":
            if elapsed_in_state >= 1.0:
                # Nota: Aqui não definimos location="TRANSIT" manualmente.
                # O change_state("MOVING_TO_DROP") vai tratar disso se não falhar.
                self.change_state("MOVING_TO_DROP")

        elif self.status == "MOVING_TO_DROP":
            if elapsed_in_state >= 2.0:
                if self.target_station is None:
                    return
                self.location_id = self.target_station
                self.change_state("DROPPING")

        elif self.status == "DROPPING":
            if elapsed_in_state >= 1.0:
                self.location_id = "DOCK"  # Ou zona de packing
                self.change_state("IDLE")

        elif self.status == "MOVING_TO_CHARGE":
            if elapsed_in_state >= 3.0:
                self.location_id = "CHARGING_STATION"
                self.change_state("CHARGING")

        elif self.status == "CHARGING":
            if elapsed_in_state >= 10.0:
                self.battery = 100.0
                self.location_id = "DOCK"
                self.change_state("IDLE")

    def publish_status(self) -> None:
        payload = RobotTelemetry(
            robot_id=self.robot_id,
            timestamp=datetime.now(UTC).isoformat(),
            location_id=self.location_id,
            battery=int(self.battery),
            status=self.status,
        )
        self.client.publish(self.topic_pub, payload.model_dump_json(), qos=MQTT_QOS)

    def run(self) -> None:
        self.connect()
        try:
            while True:
                self.update_logic()
                time.sleep(0.1)
        except KeyboardInterrupt:
            self.client.publish(
                self.availability_topic,
                json.dumps({"robot_id": self.robot_id, "status": "offline"}),
                qos=MQTT_QOS,
                retain=True,
            )
            self.client.loop_stop()
            self.client.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("group_id")
    parser.add_argument(
        "robot_id",
        nargs="?",
        default=socket.gethostname(),
    )
    args = parser.parse_args()
    bot = AMRRobot(args.group_id or GROUP_ID, args.robot_id, MQTT_BROKER)
    bot.run()
