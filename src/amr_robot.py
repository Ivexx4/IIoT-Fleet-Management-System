import argparse
import json
import random
import re
import socket
import sys
import threading
import time
from datetime import UTC, datetime
from typing import Any

import paho.mqtt.client as mqtt

from models import RobotTelemetry
from mqtt_config import GROUP_ID, MQTT_BROKER, MQTT_PORT, MQTT_QOS, create_mqtt_client
from protocol import decode_command
from task_allocator import (
    GridPoint,
    location_coordinates,
    plan_grid_route,
    segment_distance_at_time,
    segment_travel_time,
    turn_angle,
)

# --- CONFIGURAÇÕES DE SIMULAÇÃO ---
STALL_PROBABILITY = 1
MAX_TRAVEL_SPEED = 1.5
ACCELERATION = 1.0
TURN_90_SECONDS = 0.8
TURN_180_SECONDS = 1.5
TURN_BATTERY_COST = 0.03
BATTERY_PER_GRID_UNIT = 0.12
HANDLING_BATTERY_RATE = 0.04
CHARGING_RATE = 1.5
IDLE_BATTERY_RATE = 0.002
TELEMETRY_INTERVAL = 0.1
PEER_STATE_TIMEOUT = 2.0
MOVING_STATES = {
    "MOVING_TO_PICK",
    "MOVING_TO_DROP",
    "MOVING_TO_CHARGE",
    "MOVING_TO_DOCK",
}


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
        self.load_quantity = 0
        self.state_duration = 0.0
        robot_number = re.search(r"(\d+)$", robot_id)
        dock_slot = max(0, int(robot_number.group(1)) - 1) if robot_number else 0
        self.x = 0.0
        self.y = float(dock_slot)
        self.route: list[GridPoint] = []
        self.route_index = 0
        self.segment_start: GridPoint = (0, dock_slot)
        self.segment_elapsed = 0.0
        self.movement_status: str | None = None
        self.movement_target: str | None = None
        self.current_heading: GridPoint = (1, 0)
        self.turn_remaining = 0.0
        self.pending_heading: GridPoint | None = None
        self.peer_robots: dict[str, tuple[RobotTelemetry, float]] = {}
        self.peer_lock = threading.Lock()

        self.state_start_time = time.monotonic()

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
        self.topic_plan = f"warehouse/{group_id}/amr/{robot_id}/plan"
        self.planned_routes: dict[str, tuple[list[GridPoint], float, float]] = {}
        self.planned_start_at: float | None = None
        self.planned_tick_seconds = 0.0

        self.last_battery_update = time.monotonic()
        self.last_telemetry_update = self.last_battery_update

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
            client.subscribe(
                [
                    (self.topic_sub, MQTT_QOS),
                    (f"warehouse/{self.group_id}/amr/+/status", MQTT_QOS),
                    (self.topic_plan, MQTT_QOS),
                ]
            )
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
        if msg.topic == self.topic_plan:
            self._cache_planned_routes(msg.payload)
            return
        if msg.topic.endswith("/status"):
            try:
                telemetry = RobotTelemetry.model_validate_json(msg.payload)
            except ValueError as error:
                print(f"Rejected peer telemetry for {self.robot_id}: {error}")
                return
            if telemetry.robot_id != self.robot_id:
                with self.peer_lock:
                    self.peer_robots[telemetry.robot_id] = (
                        telemetry,
                        time.monotonic(),
                    )
            return

        try:
            cmd_type, shelf_byte, station_byte, quantity = decode_command(msg.payload)
            print(
                f"[{self.robot_id}] RX CMD: Type={cmd_type}, "
                f"Shelf={shelf_byte}, Station={station_byte}"
            )

            # 0x03: FORCE_CHARGE (Override)
            if cmd_type == 0x03:
                print(f"[{self.robot_id}] !!! OVERRIDE: FORCE CHARGE !!!")
                self.change_state("MOVING_TO_CHARGE")
                return

            # 0x01: EXECUTE_TASK
            if self.status in {"STALLED", "STALLED_TRAFFIC", "TURNING"} or (
                self.status in MOVING_STATES
            ):
                print(f"[{self.robot_id}] Ignored task while {self.status}")
                return

            if cmd_type == 0x01 and self.status == "IDLE":
                self.target_shelf = f"SHELF-S{shelf_byte}"
                self.target_station = f"STATION-P{station_byte}"
                self.load_quantity = quantity

                # Deixa change_state controlar a transição de localização.
                self.change_state("MOVING_TO_PICK")

        except ValueError as error:
            print(f"Rejected command for {self.robot_id}: {error}")

    def change_state(self, new_state: str) -> None:
        if new_state in MOVING_STATES:
            target = {
                "MOVING_TO_PICK": self.target_shelf,
                "MOVING_TO_DROP": self.target_station,
                "MOVING_TO_CHARGE": "CHARGING_STATION",
                "MOVING_TO_DOCK": "DOCK",
            }[new_state]
            if target is None:
                raise ValueError(f"{new_state} requires a destination")
            self._begin_movement(new_state, target)
            return

        if new_state == "PICKING":
            self.state_duration = random.uniform(1.5, 3.0)
        elif new_state == "DROPPING":
            self.state_duration = random.uniform(2.0, 4.0)
        else:
            self.state_duration = 0.0

        self.status = new_state
        self.state_start_time = time.monotonic()
        self.last_battery_update = self.state_start_time
        print(
            f"[{self.robot_id}] State changed to: {self.status} "
            f"(Loc: {self.location_id})"
        )
        self.publish_status()

    def _begin_movement(self, status: str, target: str) -> None:
        if status in {"MOVING_TO_PICK", "MOVING_TO_DROP"} and (
            random.randint(1, 100) <= STALL_PROBABILITY
        ):
            self.status = "STALLED"
            self.state_start_time = time.monotonic()
            print(f"[{self.robot_id}] Mechanical fault: STALLED")
            self.publish_status()
            return

        start = (round(self.x), round(self.y))
        destination = location_coordinates(target)
        self.route = plan_grid_route(start, destination)
        planned_route = self.planned_routes.pop(target, None)
        if planned_route is not None:
            self.client.publish(self.topic_plan, b"", qos=MQTT_QOS, retain=True)
            candidate, start_at, tick_seconds = planned_route
            if candidate[0] == start and candidate[-1] == destination:
                self.route = candidate
                self.planned_start_at = start_at
                self.planned_tick_seconds = tick_seconds
            else:
                print(
                    f"[{self.robot_id}] Ignoring mismatched planned route to {target}"
                )
                self.planned_start_at = None
                self.planned_tick_seconds = 0.0
        else:
            self.planned_start_at = None
            self.planned_tick_seconds = 0.0
        self.route_index = 1
        self.segment_start = start
        self.segment_elapsed = 0.0
        self.movement_status = status
        self.movement_target = target
        self.turn_remaining = 0.0
        self.pending_heading = None
        self.status = status
        self.location_id = "TRANSIT"
        self.state_start_time = time.monotonic()
        self.last_battery_update = self.state_start_time
        print(
            f"[{self.robot_id}] {status} to {target} "
            f"via {len(self.route) - 1} grid cells"
        )
        self.publish_status()
        if self.route_index >= len(self.route):
            self._arrive_at_destination()

    def _cache_planned_routes(self, payload: bytes) -> None:
        try:
            message = json.loads(payload.decode("utf-8"))
            legs = message["legs"]
            if not isinstance(legs, list):
                raise ValueError("Planned route legs must be a list")
            routes: dict[str, tuple[list[GridPoint], float, float]] = {}
            for leg in legs:
                target = leg["target"]
                raw_route = leg["route"]
                start_at = float(leg["start_at"])
                tick_seconds = float(leg["tick_seconds"])
                if (
                    not isinstance(target, str)
                    or not isinstance(raw_route, list)
                    or not raw_route
                    or tick_seconds <= 0
                ):
                    raise ValueError("Invalid planned route")
                route: list[GridPoint] = []
                for point in raw_route:
                    if (
                        not isinstance(point, list)
                        or len(point) != 2
                        or any(not isinstance(value, int) for value in point)
                    ):
                        raise ValueError("Planned route cells must be integer pairs")
                    route.append((point[0], point[1]))
                if route[-1] != location_coordinates(target):
                    raise ValueError(f"Planned route does not end at {target}")
                routes[target] = (
                    route,
                    time.monotonic() + start_at - time.time(),
                    tick_seconds,
                )
            if not routes:
                raise ValueError("Planned route contains no legs")
            self.planned_routes.update(routes)
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ValueError,
        ) as error:
            print(f"[{self.robot_id}] Rejected planned route: {error}")

    def _traffic_blocker(self, target: GridPoint) -> str | None:
        now = time.monotonic()
        with self.peer_lock:
            peers = list(self.peer_robots.items())
        for robot_id, (telemetry, received_at) in peers:
            if now - received_at > PEER_STATE_TIMEOUT:
                continue
            if telemetry.x is not None and telemetry.y is not None:
                occupied = (round(telemetry.x), round(telemetry.y))
            else:
                occupied = location_coordinates(telemetry.location_id)
            if occupied == target:
                return robot_id
            if (
                telemetry.next_cell_x == target[0]
                and telemetry.next_cell_y == target[1]
                and robot_id < self.robot_id
            ):
                return robot_id
        return None

    def _arrive_at_destination(self) -> None:
        target = self.movement_target
        movement_status = self.movement_status
        if target is None or movement_status is None:
            raise RuntimeError("Robot movement completed without a destination")
        self.location_id = target
        self.route = []
        self.route_index = 0
        self.planned_start_at = None
        self.planned_tick_seconds = 0.0
        self.movement_status = None
        self.movement_target = None
        next_state = {
            "MOVING_TO_PICK": "PICKING",
            "MOVING_TO_DROP": "DROPPING",
            "MOVING_TO_CHARGE": "CHARGING",
            "MOVING_TO_DOCK": "IDLE",
        }[movement_status]
        if next_state == "IDLE":
            self.target_shelf = None
            self.target_station = None
        self.change_state(next_state)

    def _update_movement(self, now: float, elapsed: float) -> None:
        if self.status == "STALLED_TRAFFIC":
            if self.route_index >= len(self.route):
                raise RuntimeError("Traffic-stalled robot has no next route cell")
            target = self.route[self.route_index]
            if self._traffic_blocker(target) is not None:
                self.battery = max(0.0, self.battery - elapsed * IDLE_BATTERY_RATE)
                return
            if self.movement_status is None:
                raise RuntimeError("Traffic-stalled robot has no movement state")
            self.status = self.movement_status
            self.state_start_time = now
            self.publish_status()

        if self.status == "TURNING":
            self.turn_remaining = max(0.0, self.turn_remaining - elapsed)
            self.battery = max(0.0, self.battery - elapsed * IDLE_BATTERY_RATE)
            if self.turn_remaining == 0.0:
                if self.pending_heading is None:
                    raise RuntimeError("Turn completed without a target heading")
                self.current_heading = self.pending_heading
                self.pending_heading = None
                self.battery = max(0.0, self.battery - TURN_BATTERY_COST)
                self.status = self.movement_status or "STALLED"
            return

        if self.planned_start_at is not None and self.route_index < len(self.route):
            step_start = (
                self.planned_start_at
                + (self.route_index - 1) * self.planned_tick_seconds
            )
            if now < step_start:
                self.battery = max(0.0, self.battery - elapsed * IDLE_BATTERY_RATE)
                return

        if self.route_index >= len(self.route):
            self._arrive_at_destination()
            return

        target = self.route[self.route_index]
        if self.planned_start_at is not None and target == self.segment_start:
            wait_end = (
                self.planned_start_at + (self.route_index) * self.planned_tick_seconds
            )
            if now >= wait_end:
                self.route_index += 1
            self.battery = max(0.0, self.battery - elapsed * IDLE_BATTERY_RATE)
            return
        blocker = self._traffic_blocker(target)
        if blocker is not None:
            self.status = "STALLED_TRAFFIC"
            self.battery = max(0.0, self.battery - elapsed * IDLE_BATTERY_RATE)
            print(f"[{self.robot_id}] Waiting for traffic from {blocker}")
            self.publish_status()
            return

        heading = (target[0] - self.segment_start[0], target[1] - self.segment_start[1])
        angle = turn_angle(self.current_heading, heading)
        if angle:
            self.turn_remaining = TURN_180_SECONDS if angle == 180 else TURN_90_SECONDS
            self.pending_heading = heading
            self.status = "TURNING"
            self.publish_status()
            return

        previous_distance = segment_distance_at_time(
            self.segment_elapsed,
            1.0,
            MAX_TRAVEL_SPEED,
            ACCELERATION,
        )
        duration = segment_travel_time(1.0, MAX_TRAVEL_SPEED, ACCELERATION)
        self.segment_elapsed = min(duration, self.segment_elapsed + elapsed)
        current_distance = segment_distance_at_time(
            self.segment_elapsed,
            1.0,
            MAX_TRAVEL_SPEED,
            ACCELERATION,
        )
        dx = target[0] - self.segment_start[0]
        dy = target[1] - self.segment_start[1]
        self.x = self.segment_start[0] + dx * current_distance
        self.y = self.segment_start[1] + dy * current_distance
        self.battery = max(
            0.0,
            self.battery
            - (current_distance - previous_distance) * BATTERY_PER_GRID_UNIT,
        )
        if self.segment_elapsed >= duration:
            self.x, self.y = float(target[0]), float(target[1])
            self.segment_start = target
            self.route_index += 1
            self.segment_elapsed = 0.0
            if self.route_index >= len(self.route):
                self._arrive_at_destination()

    def update_logic(self) -> None:
        now = time.monotonic()
        elapsed = now - self.last_battery_update
        self.last_battery_update = now
        if self.status in MOVING_STATES | {"STALLED_TRAFFIC", "TURNING"}:
            self._update_movement(now, elapsed)
        elif self.status == "STALLED":
            return
        elif self.status in {"PICKING", "DROPPING"}:
            self.battery -= elapsed * HANDLING_BATTERY_RATE
        elif self.status == "CHARGING":
            self.battery = min(100.0, self.battery + elapsed * CHARGING_RATE)
        else:
            self.battery = max(0.0, self.battery - elapsed * IDLE_BATTERY_RATE)
        self.battery = min(100.0, max(0.0, self.battery))
        interval = (
            TELEMETRY_INTERVAL
            if (self.status in MOVING_STATES | {"STALLED_TRAFFIC", "TURNING"})
            else 1.0
        )
        if now - self.last_telemetry_update >= interval:
            self.publish_status()
            self.last_telemetry_update = now

        elapsed_in_state = now - self.state_start_time
        if self.status == "IDLE":
            if self.battery <= 20:
                self.change_state("MOVING_TO_CHARGE")

        elif self.status == "PICKING":
            if elapsed_in_state >= self.state_duration:
                self.change_state("MOVING_TO_DROP")

        elif self.status == "DROPPING":
            if elapsed_in_state >= self.state_duration:
                self.load_quantity = 0
                self.target_shelf = None
                self.target_station = None
                self.change_state("IDLE")

        elif self.status == "CHARGING":
            if self.battery >= 100.0:
                self.battery = 100.0
                self.change_state("MOVING_TO_DOCK")

    def publish_status(self) -> None:
        payload = RobotTelemetry(
            robot_id=self.robot_id,
            timestamp=datetime.now(UTC).isoformat(),
            location_id=self.location_id,
            battery=int(self.battery),
            status=self.status,
            load_quantity=self.load_quantity,
            x=round(self.x, 3),
            y=round(self.y, 3),
            next_cell_x=(
                self.route[self.route_index][0]
                if self.route_index < len(self.route)
                else None
            ),
            next_cell_y=(
                self.route[self.route_index][1]
                if self.route_index < len(self.route)
                else None
            ),
        )
        self.client.publish(
            self.topic_pub,
            payload.model_dump_json(),
            qos=MQTT_QOS,
            retain=True,
        )

    def run(self) -> None:
        self.connect()
        try:
            while True:
                self.update_logic()
                time.sleep(TELEMETRY_INTERVAL)
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
