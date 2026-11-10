import argparse
import json
import threading
from typing import Any
from uuid import uuid4

import paho.mqtt.client as mqtt
from paho.mqtt.properties import Properties
from paho.mqtt.reasoncodes import ReasonCode

from models import Order
from mqtt_config import (
    MQTT_BROKER,
    MQTT_PORT,
    MQTT_QOS,
    TOPIC_ORDER_ACK,
    TOPIC_ORDER_SUBMIT,
    create_mqtt_client,
)

ORDER_ACK_TIMEOUT_SECONDS = 15.0


def send_order(
    item: str,
    quantity: int,
    station: str,
    host: str = MQTT_BROKER,
    port: int = MQTT_PORT,
) -> None:
    order = Order(
        order_id=uuid4().hex,
        item=item,
        quantity=quantity,
        pack_station=station,
    )
    subscribed = threading.Event()
    acknowledged = threading.Event()
    response: dict[str, str] = {}
    client = create_mqtt_client(f"order-client-{uuid4().hex}")
    ack_topic = f"{TOPIC_ORDER_ACK}/{order.order_id}"

    def on_connect(
        mqtt_client: mqtt.Client,
        userdata: object,
        flags: dict[str, object],
        reason_code: object,
        properties: object,
    ) -> None:
        del userdata, flags, properties
        if getattr(reason_code, "is_failure", False):
            response["error"] = f"MQTT connection failed: {reason_code}"
            acknowledged.set()
            subscribed.set()
            return
        result, _ = mqtt_client.subscribe(ack_topic, qos=MQTT_QOS)
        if result != mqtt.MQTT_ERR_SUCCESS:
            response["error"] = "Could not subscribe to the order acknowledgement"
            acknowledged.set()
            subscribed.set()

    def on_subscribe(
        mqtt_client: mqtt.Client,
        userdata: Any,
        mid: int,
        reason_code_list: list[ReasonCode],
        properties: Properties | None,
    ) -> None:
        del mqtt_client, userdata, mid, properties
        rejected = any(
            getattr(reason_code, "is_failure", False)
            for reason_code in reason_code_list
        )
        if rejected:
            response["error"] = (
                "MQTT broker rejected the order acknowledgement subscription"
            )
            acknowledged.set()
            subscribed.set()
            return
        subscribed.set()

    def on_message(
        mqtt_client: mqtt.Client,
        userdata: object,
        message: mqtt.MQTTMessage,
    ) -> None:
        del mqtt_client, userdata
        try:
            result = json.loads(message.payload.decode("utf-8"))
            if not isinstance(result, dict):
                raise ValueError("Acknowledgement payload must be a JSON object")
            if result.get("order_id") == order.order_id:
                status = result.get("status")
                if not isinstance(status, str):
                    raise ValueError("Acknowledgement has no valid status")
                response["status"] = status
                acknowledged.set()
        except (UnicodeDecodeError, ValueError) as error:
            response["error"] = f"Invalid order acknowledgement: {error}"
            acknowledged.set()

    client.on_connect = on_connect
    client.on_subscribe = on_subscribe
    client.on_message = on_message
    try:
        client.connect(host, port, 60)
        client.loop_start()
        if not subscribed.wait(ORDER_ACK_TIMEOUT_SECONDS):
            raise TimeoutError("MQTT subscription for order acknowledgement timed out")
        if "error" in response:
            raise RuntimeError(response["error"])
        info = client.publish(
            TOPIC_ORDER_SUBMIT,
            order.model_dump_json(exclude_none=True),
            qos=MQTT_QOS,
        )
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError("MQTT broker rejected the order")
        info.wait_for_publish(timeout=5)
        if not info.is_published():
            raise TimeoutError("MQTT broker did not acknowledge the order")
        if not acknowledged.wait(ORDER_ACK_TIMEOUT_SECONDS):
            raise TimeoutError("Coordinator did not acknowledge the order")
        if "error" in response:
            raise RuntimeError(response["error"])
        if response.get("status") not in {"accepted", "duplicate"}:
            raise RuntimeError(f"Coordinator rejected the order: {response}")
        print(f"Order {order.order_id} {response['status']} by {host}:{port}")
    except (OSError, RuntimeError, TimeoutError, ValueError) as error:
        print(f"Failed to submit order: {error}")
        raise
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Simular Cliente de Armazém")
    parser.add_argument(
        "--item", default="item_A", help="ID do item (ex: item_A, item_C)"
    )
    parser.add_argument("--qty", type=int, default=1, help="Quantidade")
    parser.add_argument(
        "--station", default="P1", help="Estação de destino (ex: P1, P2)"
    )
    parser.add_argument("--host", default=MQTT_BROKER, help="MQTT broker host")
    parser.add_argument("--port", type=int, default=MQTT_PORT, help="MQTT broker port")

    args = parser.parse_args()
    send_order(args.item, args.qty, args.station, args.host, args.port)
