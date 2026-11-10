import json
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt
from pydantic import ValidationError

from models import RobotTelemetry, ShelfTelemetry, StationTelemetry
from mqtt_config import GROUP_ID, MQTT_BROKER, MQTT_PORT, MQTT_QOS, create_mqtt_client

HTTP_HOST = "0.0.0.0"
HTTP_PORT = 8080
WEB_ROOT = Path(__file__).parent / "web"


class WarehouseState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.robots: dict[str, dict[str, Any]] = {}
        self.shelves: dict[str, dict[str, Any]] = {}
        self.stations: dict[str, dict[str, Any]] = {}
        self.mqtt_connected = False

    def on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: dict[str, Any],
        reason_code: Any,
        properties: Any,
    ) -> None:
        if reason_code.is_failure:
            print(f"Visualizer MQTT connection failed: {reason_code}")
            return
        client.subscribe(
            [
                (f"warehouse/{GROUP_ID}/#", MQTT_QOS),
                (f"{GROUP_ID}/internal/amr/+/availability", MQTT_QOS),
            ]
        )
        with self.lock:
            self.mqtt_connected = True
        print(f"Visualizer subscribed to warehouse/{GROUP_ID}/#")

    def on_disconnect(
        self,
        client: mqtt.Client,
        userdata: Any,
        disconnect_flags: Any,
        reason_code: Any,
        properties: Any,
    ) -> None:
        with self.lock:
            self.mqtt_connected = False

    def on_message(
        self,
        client: mqtt.Client,
        userdata: Any,
        message: mqtt.MQTTMessage,
    ) -> None:
        parts = message.topic.split("/")
        if (
            len(parts) == 5
            and parts[0] == GROUP_ID
            and parts[1:3] == ["internal", "amr"]
            and parts[4] == "availability"
        ):
            try:
                availability = json.loads(message.payload.decode("utf-8"))
                robot_id = availability["robot_id"]
                status = availability["status"]
                if (
                    not isinstance(robot_id, str)
                    or not robot_id
                    or not isinstance(status, str)
                    or status not in {"online", "offline"}
                ):
                    raise ValueError("Invalid robot availability")
            except (
                UnicodeDecodeError,
                json.JSONDecodeError,
                KeyError,
                TypeError,
                ValueError,
            ) as error:
                print(f"Rejected visualizer availability on {message.topic}: {error}")
                return
            with self.lock:
                robot = self.robots.get(robot_id)
                if robot is not None:
                    robot["availability"] = status
                    if status == "offline":
                        robot["status"] = "OFFLINE"
            return

        if len(parts) != 5 or parts[0] != "warehouse" or parts[1] != GROUP_ID:
            return
        resource, asset_id, attribute = parts[2:]
        if attribute != "status" or resource not in {"amr", "shelf", "station"}:
            return
        try:
            payload = json.loads(message.payload.decode("utf-8"))
            if resource == "amr":
                record = RobotTelemetry.model_validate(payload).model_dump()
            elif resource == "shelf":
                record = ShelfTelemetry.model_validate(payload).model_dump(
                    by_alias=True
                )
            else:
                record = StationTelemetry.model_validate(payload).model_dump()
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as error:
            print(f"Rejected visualizer telemetry on {message.topic}: {error}")
            return

        with self.lock:
            target = {
                "amr": self.robots,
                "shelf": self.shelves,
                "station": self.stations,
            }[resource]
            target[asset_id] = record

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "mqtt_connected": self.mqtt_connected,
                "robots": list(self.robots.values()),
                "shelves": list(self.shelves.values()),
                "stations": list(self.stations.values()),
            }


class VisualizerHandler(SimpleHTTPRequestHandler):
    state: WarehouseState

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] == "/api/state":
            response = json.dumps(self.state.snapshot()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)
            return
        if self.path == "/":
            self.path = "/index.html"
        super().do_GET()

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    state = WarehouseState()
    client = create_mqtt_client("warehouse-visualizer")
    client.on_connect = state.on_connect
    client.on_disconnect = state.on_disconnect
    client.on_message = state.on_message
    client.connect(MQTT_BROKER, MQTT_PORT, 60)
    client.loop_start()

    handler = type("StatefulVisualizerHandler", (VisualizerHandler,), {"state": state})
    server = ThreadingHTTPServer((HTTP_HOST, HTTP_PORT), handler)
    print(f"Warehouse visualizer available on http://localhost:{HTTP_PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
