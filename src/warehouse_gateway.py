import json
import threading
import time
from datetime import datetime
from typing import Any

import paho.mqtt.client as mqtt
from influxdb_client import InfluxDBClient, Point, WriteOptions
from pydantic import ValidationError
from redis import Redis

from models import (
    DispatchCommand,
    HealthAlert,
    RobotTelemetry,
    ShelfTelemetry,
    StationTelemetry,
)
from mqtt_config import (
    GROUP_ID,
    INFLUX_BUCKET,
    INFLUX_ORG,
    INFLUX_TOKEN,
    INFLUX_URL,
    MQTT_BROKER,
    MQTT_PORT,
    MQTT_QOS,
    REDIS_URL,
    TOPIC_CRITICAL_ALERTS,
    create_mqtt_client,
)
from protocol import encode_command, normalize_stock

TOPIC_SENSORS = f"warehouse/{GROUP_ID}/#"
TOPIC_TASK_DISPATCH = f"{GROUP_ID}/internal/tasks/dispatch"
TOPIC_GATEWAY_AVAILABILITY = f"{GROUP_ID}/internal/gateway/availability"
REDIS_PROCESSED_ALERTS = "warehouse:processed-alerts"


class WarehouseGateway:
    def __init__(self) -> None:
        if not INFLUX_TOKEN:
            raise RuntimeError("INFLUX_TOKEN is required to start the gateway")
        self.mqtt_client = create_mqtt_client(
            "warehouse-gateway",
            persistent_session=True,
            manual_ack=True,
        )
        self.mqtt_client.on_connect = self.on_connect
        self.mqtt_client.on_message = self.on_message
        self.mqtt_client.will_set(
            TOPIC_GATEWAY_AVAILABILITY,
            json.dumps({"status": "offline"}),
            qos=MQTT_QOS,
            retain=True,
        )
        self.influx_client = InfluxDBClient(
            url=INFLUX_URL,
            token=INFLUX_TOKEN,
            org=INFLUX_ORG,
        )
        self.write_api = self.influx_client.write_api(
            write_options=WriteOptions(
                batch_size=1,
                flush_interval=1_000,
                jitter_interval=0,
                retry_interval=5_000,
                max_retries=3,
                max_retry_delay=30_000,
                max_close_wait=30_000,
                exponential_base=2,
            )
        )
        self.redis = Redis.from_url(REDIS_URL, decode_responses=True)
        self.redis.ping()
        self.alert_lock = threading.Lock()

    def start(self) -> None:
        self.mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
        self.mqtt_client.loop_start()
        print(f"Gateway ready for group {GROUP_ID}")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.close()

    def close(self) -> None:
        self.mqtt_client.publish(
            TOPIC_GATEWAY_AVAILABILITY,
            json.dumps({"status": "offline"}),
            qos=MQTT_QOS,
            retain=True,
        )
        self.mqtt_client.loop_stop()
        self.mqtt_client.disconnect()
        self.write_api.close()
        self.influx_client.close()
        self.redis.close()

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
        client.subscribe(
            [
                (TOPIC_SENSORS, MQTT_QOS),
                (TOPIC_TASK_DISPATCH, MQTT_QOS),
                (TOPIC_CRITICAL_ALERTS, MQTT_QOS),
            ]
        )
        client.publish(
            TOPIC_GATEWAY_AVAILABILITY,
            json.dumps({"status": "online"}),
            qos=MQTT_QOS,
            retain=True,
        )
        print("Gateway connected to MQTT")

    def on_message(
        self,
        client: mqtt.Client,
        userdata: Any,
        message: mqtt.MQTTMessage,
    ) -> None:
        if message.topic in {TOPIC_TASK_DISPATCH, TOPIC_CRITICAL_ALERTS}:
            threading.Thread(
                target=self.process_control_message,
                args=(client, message),
                daemon=True,
            ).start()
            return
        try:
            payload = message.payload.decode("utf-8")
            if message.topic.startswith(f"warehouse/{GROUP_ID}/"):
                self.process_sensor_data(message.topic, payload)
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            ValidationError,
            ValueError,
        ) as error:
            print(f"Rejected MQTT message on {message.topic}: {error}")
            client.ack(message.mid, message.qos)
        except Exception as error:
            print(f"Failed to process MQTT message on {message.topic}: {error}")
            if message.topic != TOPIC_CRITICAL_ALERTS:
                client.ack(message.mid, message.qos)
            return
        else:
            client.ack(message.mid, message.qos)

    def process_control_message(
        self,
        client: mqtt.Client,
        message: mqtt.MQTTMessage,
    ) -> None:
        try:
            if message.topic == TOPIC_TASK_DISPATCH:
                self.process_command(
                    DispatchCommand.model_validate_json(message.payload)
                )
            elif message.topic == TOPIC_CRITICAL_ALERTS:
                self.process_alert(HealthAlert.model_validate_json(message.payload))
        except (ValidationError, ValueError, json.JSONDecodeError) as error:
            print(f"Rejected MQTT control message on {message.topic}: {error}")
            client.ack(message.mid, message.qos)
        except Exception as error:
            print(f"Failed to process MQTT control message on {message.topic}: {error}")
        else:
            client.ack(message.mid, message.qos)

    def process_sensor_data(self, topic: str, payload: str) -> None:
        data = json.loads(payload)
        topic_parts = topic.split("/")

        if (
            len(topic_parts) == 5
            and topic_parts[:3] == ["warehouse", GROUP_ID, "amr"]
            and topic_parts[4] == "status"
        ):
            telemetry = RobotTelemetry.model_validate(data)
            if telemetry.robot_id != topic_parts[3]:
                raise ValueError(
                    "AMR telemetry robot_id must match its MQTT topic identity"
                )
            point = (
                Point("Robots_status")
                .tag("robot_id", telemetry.robot_id)
                .field("status", telemetry.status)
                .field("location", telemetry.location_id)
                .field("battery", telemetry.battery)
            )
            if telemetry.x is not None and telemetry.y is not None:
                point.field("x", telemetry.x).field("y", telemetry.y)
            self.write_api.write(bucket=INFLUX_BUCKET, record=point)
            self.mqtt_client.publish(
                f"{GROUP_ID}/internal/amr/{telemetry.robot_id}/status",
                telemetry.model_dump_json(),
                qos=MQTT_QOS,
            )
            return

        if len(topic_parts) >= 6 and topic_parts[2] == "locations":
            shelf_telemetry = ShelfTelemetry.model_validate(data)
            stock_units, normalized_unit = normalize_stock(
                topic_parts[3],
                shelf_telemetry.stock,
            )
            normalized = shelf_telemetry.model_copy(
                update={
                    "stock_units": stock_units,
                    "unit": normalized_unit,
                }
            )
            point = (
                Point("Shelves")
                .tag("asset_id", shelf_telemetry.asset_id)
                .field("stock", stock_units)
            )
            self.write_api.write(bucket=INFLUX_BUCKET, record=point)
            self.mqtt_client.publish(
                f"{GROUP_ID}/internal/static/{shelf_telemetry.asset_id}/status",
                normalized.model_dump_json(by_alias=True),
                qos=MQTT_QOS,
            )
            return

        if len(topic_parts) >= 5 and topic_parts[2] == "station":
            station_telemetry = StationTelemetry.model_validate(data)
            point = (
                Point("Stations")
                .tag("station_id", station_telemetry.station_id)
                .field("status", station_telemetry.status)
                .field(
                    "is_busy",
                    int(station_telemetry.status == "PROCESSING"),
                )
            )
            self.write_api.write(bucket=INFLUX_BUCKET, record=point)
            self.mqtt_client.publish(
                f"{GROUP_ID}/internal/station/{station_telemetry.station_id}/status",
                station_telemetry.model_dump_json(),
                qos=MQTT_QOS,
            )

    def process_command(self, command: DispatchCommand) -> None:
        binary_payload = encode_command(
            command.command,
            command.target_shelf_id,
            command.target_station_id,
            command.quantity,
        )
        topic = f"warehouse/{GROUP_ID}/amr/{command.robot_id}/command"
        result = self.mqtt_client.publish(topic, binary_payload, qos=MQTT_QOS)
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(f"MQTT rejected command for {command.robot_id}")
        result.wait_for_publish(timeout=5)

        point = (
            Point("Task_History")
            .tag("robot_id", command.robot_id)
            .field("command_type", command.command)
            .field("target_shelf", str(command.target_shelf_id))
            .field("target_station", str(command.target_station_id))
        )
        self.write_api.write(bucket=INFLUX_BUCKET, record=point)
        print(f"Dispatched {command.command} to {command.robot_id}")

    def process_alert(self, alert: HealthAlert) -> str:
        with self.alert_lock:
            if self.redis.sismember(REDIS_PROCESSED_ALERTS, alert.alert_id):
                return "duplicate"
            point = (
                Point("system_alerts")
                .tag("alert_id", alert.alert_id)
                .tag("robot_id", alert.robot_id)
                .field("level", alert.level)
                .field("message", alert.message)
            )
            self.write_api.write(bucket=INFLUX_BUCKET, record=point)
            if alert.override_task:
                self.process_command(
                    DispatchCommand(
                        robot_id=alert.robot_id,
                        command=alert.override_task,
                    )
                )
            self.redis.sadd(REDIS_PROCESSED_ALERTS, alert.alert_id)
            return "accepted"


if __name__ == "__main__":
    print(f"[{datetime.now().isoformat()}] Starting Warehouse Gateway")
    WarehouseGateway().start()
