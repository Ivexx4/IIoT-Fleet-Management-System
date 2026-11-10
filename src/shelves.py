import argparse
import sys
import threading
import time
from datetime import UTC, datetime
from typing import Any

import paho.mqtt.client as mqtt

from models import (
    InfrastructureLocation,
    LocationRequest,
    RobotTelemetry,
    ShelfTelemetry,
)
from mqtt_config import (
    GROUP_ID,
    MQTT_BROKER,
    MQTT_PORT,
    MQTT_QOS,
    create_mqtt_client,
)
from task_allocator import location_coordinates


class Shelf:
    def __init__(
        self,
        group_id: str,
        zone_id: str,
        asset_id: str,
        update_time: float,
    ) -> None:
        if update_time <= 0:
            raise ValueError("update_time must be positive")
        self.group_id = group_id
        self.zone_id = zone_id
        self.asset_id = asset_id
        self.update_time = update_time
        self.config = self._get_initial_config()
        self.max_stock = self.config["stock"]
        self.lock = threading.Lock()
        self.refill_at: float | None = None
        self.robots_picking: set[str] = set()
        self.topic_sub = f"warehouse/{group_id}/amr/+/status"
        self.location_request_topic = (
            f"{group_id}/internal/infrastructure/location/request"
        )
        self.location_report_topic = f"{group_id}/internal/infrastructure/location"
        self.topic = f"warehouse/{group_id}/locations/{zone_id}/{asset_id}/status"
        self.client = create_mqtt_client(f"{group_id}-{asset_id}")
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

    def _get_initial_config(self) -> dict[str, Any]:
        item, unit, stock = {
            "storage-a": ("item_A", "units", 100),
            "storage-b": ("item_C", "kg", 200),
        }.get(self.zone_id, ("UNKNOWN", "units", 0))
        return {
            "asset id": self.asset_id,
            "type": "SHELF",
            "zone_id": self.zone_id,
            "location_id": f"SHELF-{self.asset_id}",
            "item id": item,
            "stock": stock,
            "unit": unit,
        }

    def on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: dict[str, Any],
        reason_code: Any,
        properties: Any,
    ) -> None:
        if reason_code.is_failure:
            print(f"[{self.asset_id}] MQTT connection failed: {reason_code}")
            return
        client.subscribe(
            [
                (self.topic_sub, MQTT_QOS),
                (self.location_request_topic, MQTT_QOS),
            ]
        )
        self.publish_status()

    def on_message(
        self,
        client: mqtt.Client,
        userdata: Any,
        message: mqtt.MQTTMessage,
    ) -> None:
        if message.topic == self.location_request_topic:
            try:
                LocationRequest.model_validate_json(message.payload)
                self.publish_location()
            except ValueError as error:
                print(f"[{self.asset_id}] Rejected location request: {error}")
            return
        try:
            robot = RobotTelemetry.model_validate_json(message.payload)
        except ValueError as error:
            print(f"[{self.asset_id}] Rejected robot telemetry: {error}")
            return
        if robot.location_id != f"SHELF-{self.asset_id}" or robot.status != "PICKING":
            self.robots_picking.discard(robot.robot_id)
            return
        if robot.robot_id in self.robots_picking:
            return
        self.robots_picking.add(robot.robot_id)
        with self.lock:
            stock = float(self.config["stock"])
            unit_weight = 23 if self.zone_id == "storage-b" else 1
            amount_to_take = robot.load_quantity * unit_weight
            self.config["stock"] = max(0, stock - amount_to_take)
            if self.config["stock"] == 0:
                self.refill_at = time.monotonic() + 15
        self.publish_status()

    def connect(self) -> None:
        print(f"Starting shelf {self.asset_id} ({self.zone_id})")
        self.client.connect(MQTT_BROKER, MQTT_PORT, 60)
        self.client.loop_start()

    def update_logic(self) -> None:
        now = time.monotonic()
        with self.lock:
            if self.refill_at is not None and now >= self.refill_at:
                self.config["stock"] = self.max_stock
                self.refill_at = None
                should_publish = True
            else:
                should_publish = False
        if should_publish:
            print(
                f"[{self.asset_id}] Refilled to {self.max_stock} {self.config['unit']}"
            )
            self.publish_status()

    def publish_status(self) -> None:
        with self.lock:
            data = {
                **self.config,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        data["x"], data["y"] = location_coordinates(f"SHELF-{self.asset_id}")
        payload = ShelfTelemetry.model_validate(data).model_dump_json(by_alias=True)
        result = self.client.publish(
            self.topic,
            payload,
            qos=MQTT_QOS,
            retain=True,
        )
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            print(f"[{self.asset_id}] MQTT rejected shelf telemetry")

    def publish_location(self) -> None:
        x, y = location_coordinates(f"SHELF-{self.asset_id}")
        location = InfrastructureLocation(
            asset_type="shelf",
            asset_id=self.asset_id,
            location_id=f"SHELF-{self.asset_id}",
            zone_id=self.zone_id,
            x=x,
            y=y,
        )
        result = self.client.publish(
            self.location_report_topic,
            location.model_dump_json(),
            qos=MQTT_QOS,
        )
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            print(f"[{self.asset_id}] MQTT rejected location report")

    def run(self) -> None:
        self.connect()
        last_update = 0.0
        try:
            while True:
                now = time.monotonic()
                self.update_logic()
                if now - last_update >= self.update_time:
                    self.publish_status()
                    last_update = now
                time.sleep(0.1)
        except KeyboardInterrupt:
            self.client.loop_stop()
            self.client.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Simulate a warehouse shelf")
    parser.add_argument("group_id", default=GROUP_ID)
    parser.add_argument("zone_id")
    parser.add_argument("asset_id")
    parser.add_argument("update_time", type=float)
    args = parser.parse_args()
    try:
        Shelf(args.group_id, args.zone_id, args.asset_id, args.update_time).run()
    except (OSError, ValueError) as error:
        print(f"Shelf failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
