import threading
import time
from typing import Any
from uuid import uuid4

import paho.mqtt.client as mqtt
from pydantic import ValidationError

from models import HealthAlert, RobotTelemetry
from mqtt_config import (
    GROUP_ID,
    MQTT_BROKER,
    MQTT_PORT,
    MQTT_QOS,
    TOPIC_CRITICAL_ALERTS,
    create_mqtt_client,
)

TOPIC_INTERNAL_AMR = f"{GROUP_ID}/internal/amr/+/status"
BATTERY_THRESHOLD = 15.0
STALL_TIMEOUT = 30.0
ALERT_COOLDOWN = 10.0


class RobotHealth:
    def __init__(self, data: RobotTelemetry, now: float) -> None:
        self.data = data
        self.last_move_time = now
        self.last_alert = 0.0


class SystemMonitor:
    def __init__(self) -> None:
        self.robots: dict[str, RobotHealth] = {}
        self.lock = threading.Lock()
        self.client = create_mqtt_client("system-monitor")
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

    def start(self) -> None:
        self.client.connect(MQTT_BROKER, MQTT_PORT, 60)
        self.client.loop_start()
        print(f"System Monitor started for {GROUP_ID}")
        try:
            while True:
                self.check_fleet_health()
                time.sleep(1)
        except KeyboardInterrupt:
            self.close()

    def close(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()

    def on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: dict[str, Any],
        reason_code: Any,
        properties: Any,
    ) -> None:
        if reason_code.is_failure:
            print(f"MQTT connection failed: {reason_code}")
            return
        client.subscribe(TOPIC_INTERNAL_AMR, qos=MQTT_QOS)

    def on_message(
        self,
        client: mqtt.Client,
        userdata: Any,
        message: mqtt.MQTTMessage,
    ) -> None:
        try:
            telemetry = RobotTelemetry.model_validate_json(message.payload)
            now = time.monotonic()
            with self.lock:
                health = self.robots.get(telemetry.robot_id)
                if health is None:
                    self.robots[telemetry.robot_id] = RobotHealth(telemetry, now)
                    return
                if telemetry.location_id != health.data.location_id:
                    health.last_move_time = now
                health.data = telemetry
        except (ValidationError, ValueError) as error:
            print(f"Rejected AMR telemetry on {message.topic}: {error}")

    def check_fleet_health(self) -> None:
        now = time.monotonic()
        with self.lock:
            snapshot = list(self.robots.items())
        for robot_id, health in snapshot:
            if now - health.last_alert < ALERT_COOLDOWN:
                continue
            data = health.data
            alert_message: str | None = None
            if data.battery <= BATTERY_THRESHOLD and data.status not in {
                "CHARGING",
                "MOVING_TO_CHARGE",
            }:
                alert_message = f"Battery Critical ({data.battery:.1f}%)"
            elif (
                data.status == "STALLED"
                and now - health.last_move_time >= STALL_TIMEOUT
            ):
                alert_message = (
                    f"Robot Stalled in {data.location_id} for "
                    f"{int(now - health.last_move_time)}s"
                )
            if alert_message is None:
                continue
            alert = HealthAlert(
                alert_id=uuid4().hex,
                robot_id=robot_id,
                message=alert_message,
                override_task="FORCE_CHARGE",
            )
            if self.send_alert(alert):
                with self.lock:
                    current = self.robots.get(robot_id)
                    if current is not None:
                        current.last_alert = now

    def send_alert(self, alert: HealthAlert) -> bool:
        try:
            result = self.client.publish(
                TOPIC_CRITICAL_ALERTS,
                alert.model_dump_json(),
                qos=MQTT_QOS,
            )
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                raise RuntimeError("MQTT rejected the critical alert")
            result.wait_for_publish(timeout=5)
            if not result.is_published():
                raise TimeoutError("MQTT broker did not acknowledge the critical alert")
            print(f"MQTT broker acknowledged alert {alert.alert_id}")
            return True
        except (OSError, RuntimeError, TimeoutError) as error:
            print(f"Failed to deliver critical alert {alert.alert_id}: {error}")
            return False


if __name__ == "__main__":
    SystemMonitor().start()
