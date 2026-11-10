import argparse
import json
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any

import paho.mqtt.client as mqtt

from models import RobotTelemetry, ShelfTelemetry
from mqtt_config import (
    GROUP_ID,
    MQTT_BROKER,
    MQTT_PORT,
    MQTT_QOS,
    create_mqtt_client,
)


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
        self.topic_sub = f"warehouse/{group_id}/amr/+/status"
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
        client.subscribe(self.topic_sub, qos=MQTT_QOS)
        self.publish_status()

    def on_message(
        self,
        client: mqtt.Client,
        userdata: Any,
        message: mqtt.MQTTMessage,
    ) -> None:
        try:
            robot = RobotTelemetry.model_validate_json(message.payload)
        except ValueError as error:
            print(f"[{self.asset_id}] Rejected robot telemetry: {error}")
            return
        if robot.location_id != f"SHELF-{self.asset_id}" or robot.status != "PICKING":
            return
        with self.lock:
            stock = float(self.config["stock"])
            amount_to_take = 5 * (23 if self.zone_id == "storage-b" else 1)
            self.config["stock"] = max(0, stock - amount_to_take)
            if self.config["stock"] == 0:
                self.refill_at = time.monotonic() + 2
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
            print(f"[{self.asset_id}] Refilled to {self.max_stock} {self.config['unit']}")
            self.publish_status()

    def publish_status(self) -> None:
        with self.lock:
            data = {
                **self.config,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        payload = ShelfTelemetry.model_validate(data).model_dump_json(by_alias=True)
        result = self.client.publish(
            self.topic,
            payload,
            qos=MQTT_QOS,
            retain=True,
        )
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            print(f"[{self.asset_id}] MQTT rejected shelf telemetry")

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
