import sys
from datetime import datetime
from typing import Any

import paho.mqtt.client as mqtt

from mqtt_config import GROUP_ID, MQTT_BROKER, MQTT_PORT, MQTT_QOS, create_mqtt_client


def on_connect(
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
            (f"warehouse/{GROUP_ID}/#", MQTT_QOS),
            (f"{GROUP_ID}/internal/#", MQTT_QOS),
        ]
    )
    print(f"Connected to {MQTT_BROKER}; listening to warehouse telemetry")


def on_message(
    client: mqtt.Client,
    userdata: Any,
    message: mqtt.MQTTMessage,
) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        payload = message.payload.decode("utf-8")
    except UnicodeDecodeError:
        payload = repr(message.payload)
    print(f"[{timestamp}] [{message.topic}] {payload}")


def main() -> None:
    client = create_mqtt_client("mqtt-debugger")
    client.on_connect = on_connect
    client.on_message = on_message
    try:
        client.connect(MQTT_BROKER, MQTT_PORT, 60)
        client.loop_forever()
    except (OSError, KeyboardInterrupt) as error:
        print(f"MQTT debugger stopped: {error}", file=sys.stderr)
        client.disconnect()


if __name__ == "__main__":
    main()
