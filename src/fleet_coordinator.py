import json
import socket
import threading
import time
from collections.abc import Sequence
from typing import Any, Protocol, cast
from uuid import uuid4

import paho.mqtt.client as mqtt
from pydantic import ValidationError
from redis import Redis

from models import (
    InfrastructureLocation,
    LocationRequest,
    Order,
    RobotTelemetry,
    ShelfTelemetry,
    StationTelemetry,
)
from mqtt_config import (
    GROUP_ID,
    MQTT_BROKER,
    MQTT_PORT,
    MQTT_QOS,
    REDIS_URL,
    UDP_HMAC_SECRET,
    create_mqtt_client,
)
from task_allocator import (
    WAREHOUSE_OBSTACLES,
    GridPoint,
    location_coordinates,
    minimum_cost_assignment,
    plan_multi_agent_routes,
    route_cost,
)
from udp_auth import UdpAuthenticationError, UdpAuthenticator

UDP_HOST = "0.0.0.0"
UDP_PORT_ORDERS = 9091
TOPIC_INTERNAL = f"{GROUP_ID}/internal/#"
TOPIC_DISPATCH = f"{GROUP_ID}/internal/tasks/dispatch"
TOPIC_LOCATION_REQUEST = f"{GROUP_ID}/internal/infrastructure/location/request"
TOPIC_LOCATION_REPORT = f"{GROUP_ID}/internal/infrastructure/location"
REDIS_ROBOTS = "warehouse:robots"
REDIS_SHELVES = "warehouse:shelves"
REDIS_STATIONS = "warehouse:stations"
REDIS_REMOVED_SHELVES = "warehouse:removed-shelves"
REDIS_REMOVED_STATIONS = "warehouse:removed-stations"
REDIS_PENDING_ORDERS = "warehouse:pending-orders"
REDIS_RESERVED_ROBOTS = "warehouse:reserved-robots"
REDIS_ORDER_IDS = "warehouse:order-ids"
CBS_TICK_SECONDS = 4.0
CBS_START_DELAY_SECONDS = 5.0
CBS_PICKUP_DWELL_STEPS = 2
ENQUEUE_ORDER_SCRIPT = """
if redis.call("SISMEMBER", KEYS[1], ARGV[1]) == 1 then
    return 0
end
redis.call("SADD", KEYS[1], ARGV[1])
redis.call("RPUSH", KEYS[2], ARGV[2])
return 1
"""


class RedisState(Protocol):
    def close(self) -> None: ...

    def hget(self, name: str, key: str) -> str | None: ...

    def hgetall(self, name: str) -> dict[str, str]: ...

    def hdel(self, name: str, *keys: str) -> int: ...

    def hset(self, name: str, key: str, value: str) -> int: ...

    def eval(self, script: str, numkeys: int, *keys_and_args: str) -> int: ...

    def lrange(self, name: str, start: int, end: int) -> list[str]: ...

    def lrem(self, name: str, count: int, value: str) -> int: ...

    def ping(self) -> Any: ...

    def rpush(self, name: str, *values: str) -> int: ...

    def sadd(self, name: str, *values: str) -> int: ...

    def sismember(self, name: str, value: str) -> bool: ...

    def srem(self, name: str, *values: str) -> int: ...

    def smembers(self, name: str) -> set[str]: ...


class FleetCoordinator:
    def __init__(self) -> None:
        self.udp_auth = UdpAuthenticator(UDP_HMAC_SECRET)
        self.redis: RedisState = cast(
            RedisState,
            Redis.from_url(REDIS_URL, decode_responses=True),
        )
        self.redis.ping()
        self.client = create_mqtt_client("fleet-coordinator")
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.udp_sock.bind((UDP_HOST, UDP_PORT_ORDERS))
        self.lock = threading.Lock()
        self.gateway_online = False
        self.last_discovery_request: dict[str, float] = {}
        self.discovery_interval = 30.0
        self.discovery_retry_interval = 5.0

    def start(self) -> None:
        self.client.connect(MQTT_BROKER, MQTT_PORT, 60)
        self.client.loop_start()
        threading.Thread(target=self.run_udp_server, daemon=True).start()
        print(f"Fleet Coordinator started for {GROUP_ID}")
        try:
            while True:
                self.process_logic()
                self.request_locations_periodically()
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
        self.request_infrastructure_locations()

    def request_infrastructure_locations(self) -> None:
        request = LocationRequest(request_id=uuid4().hex)
        result = self.client.publish(
            TOPIC_LOCATION_REQUEST,
            request.model_dump_json(),
            qos=MQTT_QOS,
        )
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            print("MQTT rejected infrastructure location request")
            return
        print(f"Requested infrastructure locations ({request.request_id})")
        self.last_discovery_request["*"] = time.monotonic()

    def request_locations_periodically(self) -> None:
        now = time.monotonic()
        if now - self.last_discovery_request.get("*", 0.0) >= self.discovery_interval:
            self.request_infrastructure_locations()

    def _request_location_if_unknown(self, asset_type: str, asset_id: str) -> None:
        removed_key = (
            REDIS_REMOVED_SHELVES if asset_type == "shelf" else REDIS_REMOVED_STATIONS
        )
        if self.redis.sismember(removed_key, asset_id):
            return
        state_key = REDIS_SHELVES if asset_type == "shelf" else REDIS_STATIONS
        state_json = self.redis.hget(state_key, asset_id)
        if state_json:
            try:
                state = json.loads(state_json)
                if (
                    state.get("location_id") not in {None, "UNKNOWN"}
                    and state.get("x") is not None
                    and state.get("y") is not None
                ):
                    return
            except json.JSONDecodeError as error:
                print(f"Invalid cached {asset_type} state for {asset_id}: {error}")
        now = time.monotonic()
        key = f"{asset_type}:{asset_id}"
        if (
            now
            - self.last_discovery_request.get(
                key,
                float("-inf"),
            )
            < self.discovery_retry_interval
        ):
            return
        result = self.client.publish(
            TOPIC_LOCATION_REQUEST,
            LocationRequest(request_id=uuid4().hex).model_dump_json(),
            qos=MQTT_QOS,
        )
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            print(f"MQTT rejected location request for {asset_type} {asset_id}")
            return
        self.last_discovery_request[key] = now

    def add_shelf(self, shelf: ShelfTelemetry) -> None:
        with self.lock:
            self.redis.srem(REDIS_REMOVED_SHELVES, shelf.asset_id)
            self.redis.hset(
                REDIS_SHELVES,
                shelf.asset_id,
                shelf.model_dump_json(by_alias=True),
            )

    def remove_shelf(self, asset_id: str) -> bool:
        with self.lock:
            removed = bool(self.redis.hdel(REDIS_SHELVES, asset_id))
            self.redis.sadd(REDIS_REMOVED_SHELVES, asset_id)
            return removed

    def add_station(self, station: StationTelemetry) -> None:
        with self.lock:
            self.redis.srem(REDIS_REMOVED_STATIONS, station.station_id)
            self.redis.hset(
                REDIS_STATIONS,
                station.station_id,
                station.model_dump_json(),
            )

    def remove_station(self, station_id: str) -> bool:
        with self.lock:
            removed = bool(self.redis.hdel(REDIS_STATIONS, station_id))
            self.redis.sadd(REDIS_REMOVED_STATIONS, station_id)
            return removed

    def _register_location(self, location: InfrastructureLocation) -> None:
        if location.asset_type == "shelf":
            if self.redis.sismember(REDIS_REMOVED_SHELVES, location.asset_id):
                return
            existing_json = self.redis.hget(REDIS_SHELVES, location.asset_id)
            if existing_json:
                existing_shelf = ShelfTelemetry.model_validate_json(existing_json)
                self.add_shelf(
                    existing_shelf.model_copy(
                        update={
                            "zone_id": location.zone_id or existing_shelf.zone_id,
                            "location_id": location.location_id,
                            "x": location.x,
                            "y": location.y,
                        }
                    )
                )
                return
            shelf_state = ShelfTelemetry.model_validate(
                {
                    "asset id": location.asset_id,
                    "item id": "UNKNOWN",
                    "zone_id": location.zone_id or "UNKNOWN",
                    "location_id": location.location_id,
                    "x": location.x,
                    "y": location.y,
                    "stock": 0,
                }
            )
            self.add_shelf(shelf_state)
        else:
            if self.redis.sismember(REDIS_REMOVED_STATIONS, location.asset_id):
                return
            existing_json = self.redis.hget(REDIS_STATIONS, location.asset_id)
            if existing_json:
                existing_station = StationTelemetry.model_validate_json(existing_json)
                self.add_station(
                    existing_station.model_copy(
                        update={
                            "location_id": location.location_id,
                            "x": location.x,
                            "y": location.y,
                        }
                    )
                )
                return
            station_state = StationTelemetry(
                station_id=location.asset_id,
                location_id=location.location_id,
                x=location.x,
                y=location.y,
                status="UNKNOWN",
            )
            self.add_station(station_state)

    def on_message(
        self,
        client: mqtt.Client,
        userdata: Any,
        message: mqtt.MQTTMessage,
    ) -> None:
        try:
            topic_parts = message.topic.split("/")
            payload = json.loads(message.payload.decode("utf-8"))
            if message.topic == TOPIC_LOCATION_REPORT:
                location = InfrastructureLocation.model_validate(payload)
                self._register_location(location)
                if (
                    location.asset_type == "shelf"
                    and self.redis.sismember(
                        REDIS_REMOVED_SHELVES,
                        location.asset_id,
                    )
                ) or (
                    location.asset_type == "station"
                    and self.redis.sismember(
                        REDIS_REMOVED_STATIONS,
                        location.asset_id,
                    )
                ):
                    return
                print(
                    f"Registered {location.asset_type} {location.asset_id} "
                    f"at {location.location_id} ({location.x}, {location.y})"
                )
                return
            if (
                len(topic_parts) >= 4
                and topic_parts[2] == "gateway"
                and topic_parts[-1] == "availability"
            ):
                self.gateway_online = payload.get("status") == "online"
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
                if shelf.location_id == "UNKNOWN" or shelf.x is None or shelf.y is None:
                    self._request_location_if_unknown("shelf", shelf.asset_id)
                if not self.redis.sismember(REDIS_REMOVED_SHELVES, shelf.asset_id):
                    self.add_shelf(shelf)
                return

            if (
                len(topic_parts) >= 5
                and topic_parts[2:3] == ["station"]
                and topic_parts[-1] == "status"
            ):
                station = StationTelemetry.model_validate(payload)
                if (
                    station.location_id == "UNKNOWN"
                    or station.x is None
                    or station.y is None
                ):
                    self._request_location_if_unknown("station", station.station_id)
                if not self.redis.sismember(
                    REDIS_REMOVED_STATIONS,
                    station.station_id,
                ):
                    self.add_station(station)
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as error:
            print(f"Rejected internal MQTT message on {message.topic}: {error}")
        except Exception as error:
            print(f"Failed to update coordinator state from MQTT: {error}")

    def run_udp_server(self) -> None:
        while True:
            try:
                datagram, address = self.udp_sock.recvfrom(4_096)
            except OSError:
                return
            try:
                payload = self.udp_auth.open(datagram)
            except UdpAuthenticationError as error:
                print(f"Rejected unauthenticated UDP order from {address}: {error}")
                continue
            try:
                order = Order.model_validate_json(payload)
                order_id = order.order_id or uuid4().hex
                order_record = {
                    **order.model_dump(exclude_none=True),
                    "order_id": order_id,
                }
                self.redis.eval(
                    ENQUEUE_ORDER_SCRIPT,
                    2,
                    REDIS_ORDER_IDS,
                    REDIS_PENDING_ORDERS,
                    order_id,
                    json.dumps(order_record),
                )
                response = {"order_id": order_id, "status": "accepted"}
                self.udp_sock.sendto(
                    self.udp_auth.seal(json.dumps(response).encode("utf-8")),
                    address,
                )
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
        if not self.gateway_online:
            return
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
        selected_stations: set[str] = set()
        for raw_order in pending:
            order_data = json.loads(raw_order)
            order = Order.model_validate(order_data)
            if order.pack_station in selected_stations:
                continue
            matching_shelves = [
                (shelf_id, shelf)
                for shelf_id, shelf in parsed_shelves.items()
                if shelf.item_id == order.item
                and (
                    shelf.stock_units
                    if shelf.stock_units is not None
                    else int(shelf.stock)
                )
                >= order.quantity
            ]
            if matching_shelves:
                shelf_id, _ = matching_shelves[0]
                order_rows.append((raw_order, order_data, order, shelf_id))
                selected_stations.add(order.pack_station)
            if len(order_rows) >= len(available_robots):
                break
        if not order_rows:
            return

        costs: list[list[int | float]] = []
        shelf_choices: dict[tuple[int, int], str] = {}
        for robot_index, (_, robot) in enumerate(available_robots):
            origin = (
                (round(robot.x), round(robot.y))
                if robot.x is not None and robot.y is not None
                else robot.location_id
            )
            cost_row: list[int | float] = []
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
                    >= order.quantity
                ]
                best_shelf = min(
                    candidates,
                    key=lambda shelf_id: route_cost(
                        origin,
                        shelf_id,
                        order.pack_station,
                    ),
                )
                shelf_choices[(robot_index, order_index)] = best_shelf
                cost_row.append(route_cost(origin, best_shelf, order.pack_station))
            costs.append(cost_row)

        assignments = minimum_cost_assignment(costs)
        selected: list[tuple[int, int, str, RobotTelemetry]] = []
        for robot_index, order_index in assignments:
            robot_id, robot = available_robots[robot_index]
            selected.append((robot_index, order_index, robot_id, robot))
        starts: dict[str, GridPoint] = {}
        waypoint_map: dict[str, Sequence[GridPoint]] = {}
        for robot_index, order_index, robot_id, robot in selected:
            fallback = location_coordinates(robot.location_id)
            starts[robot_id] = (
                round(robot.x) if robot.x is not None else fallback[0],
                round(robot.y) if robot.y is not None else fallback[1],
            )
            shelf = location_coordinates(
                f"SHELF-{shelf_choices[(robot_index, order_index)]}"
            )
            station = location_coordinates(order_rows[order_index][2].pack_station)
            waypoint_map[robot_id] = (shelf, station)
        try:
            planned_routes = plan_multi_agent_routes(
                starts,
                waypoint_map,
                obstacles=WAREHOUSE_OBSTACLES,
                waypoint_dwell_steps=CBS_PICKUP_DWELL_STEPS,
            )
        except ValueError as error:
            print(f"Deferred task dispatch because CBS could not plan routes: {error}")
            return

        route_start = time.time() + CBS_START_DELAY_SECONDS
        planned_messages: dict[str, dict[str, Any]] = {}
        for robot_index, order_index, robot_id, _ in selected:
            route = planned_routes[robot_id]
            shelf = waypoint_map[robot_id][0]
            shelf_index = next(
                index for index, cell in enumerate(route) if cell == shelf
            )
            station_start_index = shelf_index + CBS_PICKUP_DWELL_STEPS
            station_route = route[station_start_index:]
            if not station_route or station_route[0] != shelf:
                raise RuntimeError(f"CBS route for {robot_id} omitted its station leg")
            order = order_rows[order_index][2]
            shelf_id = shelf_choices[(robot_index, order_index)]
            planned_messages[robot_id] = {
                "legs": [
                    {
                        "target": f"SHELF-{shelf_id}",
                        "route": route[: shelf_index + 1],
                        "start_at": route_start,
                        "tick_seconds": CBS_TICK_SECONDS,
                    },
                    {
                        "target": f"STATION-{order.pack_station}",
                        "route": station_route,
                        "start_at": route_start
                        + station_start_index * CBS_TICK_SECONDS,
                        "tick_seconds": CBS_TICK_SECONDS,
                    },
                ]
            }

        for robot_index, order_index in assignments:
            raw_order, order_data, order, _ = order_rows[order_index]
            robot_id, _ = available_robots[robot_index]
            plan_topic = f"warehouse/{GROUP_ID}/amr/{robot_id}/plan"
            plan_info = self.client.publish(
                plan_topic,
                json.dumps(planned_messages[robot_id]),
                qos=MQTT_QOS,
                retain=True,
            )
            if plan_info.rc != mqtt.MQTT_ERR_SUCCESS:
                print(f"MQTT rejected planned route for {robot_id}")
                continue
            plan_info.wait_for_publish(timeout=5)
            command = {
                "robot_id": robot_id,
                "command": "EXECUTE_TASK",
                "target_shelf_id": shelf_choices[(robot_index, order_index)],
                "target_station_id": order.pack_station,
                "quantity": order.quantity,
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
                f"Assigned order {order_data['order_id']} ({order.item}) to {robot_id}"
            )


if __name__ == "__main__":
    FleetCoordinator().start()
