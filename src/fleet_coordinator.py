import json
import socket
import threading
import time
from typing import Any
from uuid import uuid4

import paho.mqtt.client as mqtt
from pydantic import ValidationError
from redis import Redis

from models import Order, RobotTelemetry, ShelfTelemetry
from mqtt_config import (
    GROUP_ID,
    MQTT_BROKER,
    MQTT_PORT,
    MQTT_QOS,
    REDIS_URL,
    create_mqtt_client,
)
from task_allocator import minimum_cost_assignment, route_cost

UDP_HOST = "0.0.0.0"
UDP_PORT_ORDERS = 9091
TOPIC_INTERNAL = f"{GROUP_ID}/internal/#"
TOPIC_DISPATCH = f"{GROUP_ID}/internal/tasks/dispatch"
REDIS_ROBOTS = "warehouse:robots"
REDIS_SHELVES = "warehouse:shelves"
REDIS_PENDING_ORDERS = "warehouse:pending-orders"
REDIS_RESERVED_ROBOTS = "warehouse:reserved-robots"
REDIS_ORDER_IDS = "warehouse:order-ids"


class FleetCoordinator:
    def __init__(self) -> None:
        self.redis: Redis[str] = Redis.from_url(REDIS_URL, decode_responses=True)
        self.redis.ping()
        self.client = create_mqtt_client("fleet-coordinator")
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.udp_sock.bind((UDP_HOST, UDP_PORT_ORDERS))
        self.lock = threading.Lock()

    def start(self) -> None:
        self.client.connect(MQTT_BROKER, MQTT_PORT, 60)
        self.client.loop_start()
        threading.Thread(target=self.run_udp_server, daemon=True).start()
        print(f"Fleet Coordinator started for {GROUP_ID}")
        try:
            while True:
                self.process_logic()
                time.sleep(1)
        except KeyboardInterrupt:
            self.close()

    def close(self) -> None:
        self.udp_sock.close()
        self.client.loop_stop()
        self.client.disconnect()
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
        client.subscribe(TOPIC_INTERNAL, qos=MQTT_QOS)
        print(f"Listening for fleet state on {TOPIC_INTERNAL}")

    def on_message(
        self,
        client: mqtt.Client,
        userdata: Any,
        message: mqtt.MQTTMessage,
    ) -> None:
        try:
            topic_parts = message.topic.split("/")
            payload = json.loads(message.payload.decode("utf-8"))
            if len(topic_parts) >= 5 and topic_parts[2:4] == ["amr", ""]:
                return
            if len(topic_parts) >= 5 and topic_parts[2] == "amr":
                robot_id = payload.get("robot_id")
                if topic_parts[-1] == "availability":
                    if robot_id and payload.get("status") == "offline":
                        with self.lock:
                            robot_json = self.redis.hget(REDIS_ROBOTS, robot_id)
                            if robot_json:
                                robot = json.loads(robot_json)
                                robot["availability"] = "offline"
                                self.redis.hset(
                                    REDIS_ROBOTS,
                                    robot_id,
                                    json.dumps(robot),
                                )
                    return
                telemetry = RobotTelemetry.model_validate(payload)
                state = telemetry.model_dump()
                state["availability"] = "online"
                with self.lock:
                    self.redis.hset(
                        REDIS_ROBOTS,
                        telemetry.robot_id,
                        json.dumps(state),
                    )
                    if telemetry.status == "IDLE":
                        self.redis.srem(REDIS_RESERVED_ROBOTS, telemetry.robot_id)
                return

            if len(topic_parts) >= 5 and topic_parts[2:3] == ["static"]:
                shelf = ShelfTelemetry.model_validate(payload)
                with self.lock:
                    self.redis.hset(
                        REDIS_SHELVES,
                        shelf.asset_id,
                        shelf.model_dump_json(by_alias=True),
                    )
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as error:
            print(f"Rejected internal MQTT message on {message.topic}: {error}")
        except Exception as error:
            print(f"Failed to update coordinator state from MQTT: {error}")

    def run_udp_server(self) -> None:
        while True:
            try:
                payload, address = self.udp_sock.recvfrom(4_096)
            except OSError:
                return
            try:
                order = Order.model_validate_json(payload)
                order_id = order.order_id or uuid4().hex
                already_queued = not self.redis.sadd(REDIS_ORDER_IDS, order_id)
                if not already_queued:
                    order_record = {
                        **order.model_dump(),
                        "order_id": order_id,
                    }
                    self.redis.rpush(REDIS_PENDING_ORDERS, json.dumps(order_record))
                response = {"order_id": order_id, "status": "accepted"}
                self.udp_sock.sendto(json.dumps(response).encode("utf-8"), address)
                print(f"Queued order {order_id}: {order.item} x{order.quantity}")
            except (ValidationError, json.JSONDecodeError) as error:
                print(f"Rejected UDP order from {address}: {error}")
                self.udp_sock.sendto(
                    b'{"status":"rejected"}',
                    address,
                )
            except Exception as error:
                print(f"Failed to enqueue UDP order from {address}: {error}")

    def process_logic(self) -> None:
        with self.lock:
            robots = self.redis.hgetall(REDIS_ROBOTS)
            shelves = self.redis.hgetall(REDIS_SHELVES)
            pending = self.redis.lrange(REDIS_PENDING_ORDERS, 0, 49)
            reserved = self.redis.smembers(REDIS_RESERVED_ROBOTS)

        available_robots: list[tuple[str, RobotTelemetry]] = []
        for robot_id, robot_json in robots.items():
            state = json.loads(robot_json)
            if (
                robot_id not in reserved
                and state.get("availability") == "online"
                and state.get("status") == "IDLE"
                and float(state.get("battery", 0)) > 15
            ):
                available_robots.append(
                    (robot_id, RobotTelemetry.model_validate(state))
                )
        if not available_robots or not pending:
            return

        parsed_shelves = {
            shelf_id: ShelfTelemetry.model_validate_json(shelf_json)
            for shelf_id, shelf_json in shelves.items()
        }
        order_rows: list[tuple[str, dict[str, Any], Order, str]] = []
        for raw_order in pending:
            order_data = json.loads(raw_order)
            order = Order.model_validate(order_data)
            matching_shelves = [
                (shelf_id, shelf)
                for shelf_id, shelf in parsed_shelves.items()
                if shelf.item_id == order.item
                and (
                    shelf.stock_units
                    if shelf.stock_units is not None
                    else int(shelf.stock)
                )
                >= 5
            ]
            if matching_shelves:
                shelf_id, _ = matching_shelves[0]
                order_rows.append((raw_order, order_data, order, shelf_id))
            if len(order_rows) >= len(available_robots):
                break
        if not order_rows:
            return

        costs: list[list[int]] = []
        shelf_choices: dict[tuple[int, int], str] = {}
        for robot_index, (_, robot) in enumerate(available_robots):
            cost_row: list[int] = []
            for order_index, (_, _, order, _) in enumerate(order_rows):
                candidates = [
                    shelf_id
                    for shelf_id, shelf in parsed_shelves.items()
                    if shelf.item_id == order.item
                    and (
                        shelf.stock_units
                        if shelf.stock_units is not None
                        else int(shelf.stock)
                    )
                    >= 5
                ]
                best_shelf = min(
                    candidates,
                    key=lambda shelf_id: route_cost(
                        robot.location_id,
                        shelf_id,
                        order.pack_station,
                    ),
                )
                shelf_choices[(robot_index, order_index)] = best_shelf
                cost_row.append(
                    route_cost(robot.location_id, best_shelf, order.pack_station)
                )
            costs.append(cost_row)

        assignments = minimum_cost_assignment(costs)
        for robot_index, order_index in assignments:
            raw_order, order_data, order, _ = order_rows[order_index]
            robot_id, _ = available_robots[robot_index]
            command = {
                "robot_id": robot_id,
                "command": "EXECUTE_TASK",
                "target_shelf_id": shelf_choices[(robot_index, order_index)],
                "target_station_id": order.pack_station,
            }
            info = self.client.publish(
                TOPIC_DISPATCH,
                json.dumps(command),
                qos=MQTT_QOS,
            )
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                print(f"MQTT rejected task dispatch for {robot_id}")
                continue
            info.wait_for_publish(timeout=5)
            with self.lock:
                self.redis.sadd(REDIS_RESERVED_ROBOTS, robot_id)
                self.redis.lrem(REDIS_PENDING_ORDERS, 1, raw_order)
            print(
                f"Assigned order {order_data['order_id']} "
                f"({order.item}) to {robot_id}"
            )


if __name__ == "__main__":
    FleetCoordinator().start()
