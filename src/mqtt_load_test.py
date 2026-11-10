import argparse
import json
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from uuid import uuid4

import paho.mqtt.client as mqtt
from paho.mqtt.properties import Properties
from paho.mqtt.reasoncodes import ReasonCode
from redis import Redis

from fleet_coordinator import REDIS_ROBOTS
from models import RobotTelemetry
from mqtt_config import (
    GROUP_ID,
    MQTT_BROKER,
    MQTT_PORT,
    MQTT_QOS,
    REDIS_URL,
    create_mqtt_client,
)

DEFAULT_AMR_COUNT = 200
DEFAULT_MESSAGES_PER_AMR = 10
DEFAULT_INTERVAL_MS = 100
DEFAULT_TIMEOUT_SECONDS = 60.0
MAX_AMR_COUNT = 1_000
MAX_MESSAGES_PER_AMR = 1_000
TOPIC_COMPONENT_AVAILABILITY = f"{GROUP_ID}/internal/+/availability"
TOPIC_GATEWAY_AMR_STATUS = f"{GROUP_ID}/internal/amr/+/status"


class LoadTest:
    def __init__(
        self,
        amr_count: int,
        messages_per_amr: int,
        interval_ms: int,
        timeout_seconds: float,
    ) -> None:
        if not 1 <= amr_count <= MAX_AMR_COUNT:
            raise ValueError(f"amrs must be between 1 and {MAX_AMR_COUNT}")
        if not 1 <= messages_per_amr <= MAX_MESSAGES_PER_AMR:
            raise ValueError(
                f"messages per AMR must be between 1 and {MAX_MESSAGES_PER_AMR}"
            )
        if interval_ms < 0 or timeout_seconds <= 0:
            raise ValueError("interval and timeout must be non-negative/positive")

        self.amr_count = amr_count
        self.messages_per_amr = messages_per_amr
        self.interval_seconds = interval_ms / 1_000
        self.timeout_seconds = timeout_seconds
        self.run_id = uuid4().hex[:8]
        self.robot_ids = [
            f"LOAD-{self.run_id}-{index:04d}" for index in range(amr_count)
        ]
        self.sent_at_ns: dict[str, int] = {}
        self.final_timestamps: dict[str, str] = {}
        self.gateway_latencies_ms: list[float] = []
        self.received_timestamps: set[str] = set()
        self.availability: set[str] = set()
        self.condition = threading.Condition()
        self.ready = threading.Event()
        self.subscription_ready = threading.Event()
        self.callback_errors: list[str] = []
        self.observer = create_mqtt_client(f"load-observer-{uuid4().hex}")
        self.observer.on_connect = self._on_connect
        self.observer.on_subscribe = self._on_subscribe
        self.observer.on_message = self._on_message
        self.redis: Redis = Redis.from_url(REDIS_URL, decode_responses=True)
        self.publish_latencies_ms: list[float] = []

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: dict[str, Any],
        reason_code: Any,
        properties: Any,
    ) -> None:
        del userdata, flags, properties
        if reason_code.is_failure:
            self.callback_errors.append(f"Observer connection failed: {reason_code}")
            self.ready.set()
            return
        result, _ = client.subscribe(
            [
                (TOPIC_COMPONENT_AVAILABILITY, MQTT_QOS),
                (TOPIC_GATEWAY_AMR_STATUS, MQTT_QOS),
            ]
        )
        if result != mqtt.MQTT_ERR_SUCCESS:
            self.callback_errors.append("Observer subscription was rejected")
            self.ready.set()

    def _on_subscribe(
        self,
        client: mqtt.Client,
        userdata: Any,
        mid: int,
        reason_code_list: list[ReasonCode],
        properties: Properties | None,
    ) -> None:
        del client, userdata, mid, properties
        if any(reason_code.is_failure for reason_code in reason_code_list):
            self.callback_errors.append("Broker rejected a benchmark subscription")
        self.subscription_ready.set()

    def _on_message(
        self,
        client: mqtt.Client,
        userdata: Any,
        message: mqtt.MQTTMessage,
    ) -> None:
        del client, userdata
        try:
            payload = json.loads(message.payload)
            if not isinstance(payload, dict):
                raise TypeError("benchmark observation must be a JSON object")
            if message.topic.endswith("/availability"):
                if payload.get("status") == "online":
                    with self.condition:
                        self.availability.add(message.topic)
                        if len(self.availability) >= 2:
                            self.ready.set()
                return
            timestamp = payload.get("timestamp")
            if isinstance(timestamp, str) and timestamp in self.sent_at_ns:
                with self.condition:
                    if timestamp not in self.received_timestamps:
                        latency_ms = (
                            time.time_ns() - self.sent_at_ns[timestamp]
                        ) / 1_000_000
                        self.received_timestamps.add(timestamp)
                        self.gateway_latencies_ms.append(latency_ms)
                    self.condition.notify_all()
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as error:
            self.callback_errors.append(f"Invalid benchmark observation: {error}")

    def wait_until_ready(self) -> None:
        self.observer.connect(MQTT_BROKER, MQTT_PORT, 60)
        self.observer.loop_start()
        if not self.subscription_ready.wait(self.timeout_seconds):
            raise TimeoutError("Load observer subscription timed out")
        if not self.ready.wait(self.timeout_seconds):
            raise TimeoutError(
                "Gateway and coordinator must both report online on MQTT"
            )
        self.redis.ping()

    def _connect_publisher(self, amr_index: int) -> mqtt.Client:
        client = create_mqtt_client(f"load-amr-{amr_index:04d}-{uuid4().hex[:8]}")
        connected = threading.Event()
        errors: list[str] = []

        def on_connect(
            mqtt_client: mqtt.Client,
            userdata: Any,
            flags: dict[str, Any],
            reason_code: Any,
            properties: Any,
        ) -> None:
            del mqtt_client, userdata, flags, properties
            if reason_code.is_failure:
                errors.append(f"AMR {amr_index} connection failed: {reason_code}")
            connected.set()

        client.on_connect = on_connect
        client.connect(MQTT_BROKER, MQTT_PORT, 60)
        client.loop_start()
        if not connected.wait(self.timeout_seconds):
            client.loop_stop()
            raise TimeoutError(f"AMR {amr_index} MQTT connection timed out")
        if errors:
            client.loop_stop()
            raise RuntimeError(errors[0])
        return client

    def _publish_amr(
        self,
        amr_index: int,
        client: mqtt.Client,
        start_barrier: threading.Barrier,
    ) -> None:
        robot_id = self.robot_ids[amr_index]
        topic = f"warehouse/{GROUP_ID}/amr/{robot_id}/status"
        start_barrier.wait(timeout=self.timeout_seconds)
        for sequence in range(self.messages_per_amr):
            sent_at_ns = time.time_ns()
            timestamp = f"{sent_at_ns}:{amr_index}:{sequence}"
            self.sent_at_ns[timestamp] = sent_at_ns
            self.final_timestamps[robot_id] = timestamp
            telemetry = RobotTelemetry(
                robot_id=robot_id,
                timestamp=timestamp,
                location_id=f"GRID-{amr_index % 20}-{sequence % 20}",
                battery=90,
                status="MOVING_TO_PICK",
                x=float(amr_index % 20),
                y=float(sequence % 20),
            )
            started = time.perf_counter()
            info = client.publish(topic, telemetry.model_dump_json(), qos=MQTT_QOS)
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                raise RuntimeError(f"Broker rejected telemetry from {robot_id}")
            info.wait_for_publish(timeout=self.timeout_seconds)
            if not info.is_published():
                raise TimeoutError(
                    f"Broker did not acknowledge telemetry from {robot_id}"
                )
            self.publish_latencies_ms.append((time.perf_counter() - started) * 1_000)
            if self.interval_seconds:
                time.sleep(self.interval_seconds)

    def _wait_for_gateway(self, expected: int) -> float:
        deadline = time.monotonic() + self.timeout_seconds
        with self.condition:
            while len(self.received_timestamps) < expected:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.condition.wait(remaining)
        return time.perf_counter()

    def _wait_for_coordinator(self, final_timestamps: dict[str, str]) -> list[float]:
        deadline = time.monotonic() + self.timeout_seconds
        pending = dict(final_timestamps)
        latencies: list[float] = []
        while pending and time.monotonic() < deadline:
            for robot_id, expected_timestamp in list(pending.items()):
                state = self.redis.hget(REDIS_ROBOTS, robot_id)
                if state is None:
                    continue
                if not isinstance(state, str):
                    raise TypeError("Redis returned a non-string AMR state")
                try:
                    state_payload = json.loads(state)
                except json.JSONDecodeError:
                    continue
                if not isinstance(state_payload, dict):
                    continue
                actual_timestamp = state_payload.get("timestamp")
                if actual_timestamp == expected_timestamp:
                    latencies.append(
                        (time.time_ns() - self.sent_at_ns[expected_timestamp])
                        / 1_000_000
                    )
                    del pending[robot_id]
            if pending:
                time.sleep(0.01)
        if pending:
            self.callback_errors.append(
                f"Coordinator did not update {len(pending)} AMR states"
            )
        return latencies

    def run(self) -> dict[str, Any]:
        clients: list[mqtt.Client] = []
        publish_start: list[float] = []
        try:
            self.wait_until_ready()
            connect_workers = min(self.amr_count, 64)
            with ThreadPoolExecutor(max_workers=connect_workers) as executor:
                clients = list(
                    executor.map(self._connect_publisher, range(self.amr_count))
                )

            def mark_publish_start() -> None:
                publish_start.append(time.perf_counter())

            barrier = threading.Barrier(self.amr_count, action=mark_publish_start)
            with ThreadPoolExecutor(max_workers=self.amr_count) as executor:
                futures = [
                    executor.submit(self._publish_amr, index, client, barrier)
                    for index, client in enumerate(clients)
                ]
                for future in as_completed(futures):
                    future.result()

            publish_duration = time.perf_counter() - publish_start[0]
            expected_messages = self.amr_count * self.messages_per_amr
            gateway_completed_at = self._wait_for_gateway(expected_messages)
            coordinator_latencies = self._wait_for_coordinator(self.final_timestamps)
            gateway_duration = gateway_completed_at - publish_start[0]
            report = {
                "amrs": self.amr_count,
                "messages_per_amr": self.messages_per_amr,
                "messages_expected": expected_messages,
                "gateway_messages_received": len(self.received_timestamps),
                "gateway_messages_lost": expected_messages
                - len(self.received_timestamps),
                "publish_duration_seconds": round(publish_duration, 3),
                "gateway_end_to_end_throughput_per_second": round(
                    len(self.received_timestamps) / gateway_duration,
                    2,
                ),
                "broker_puback_latency_ms": _latency_summary(self.publish_latencies_ms),
                "gateway_end_to_end_latency_ms": _latency_summary(
                    self.gateway_latencies_ms
                ),
                "coordinator_state_latency_ms": _latency_summary(coordinator_latencies),
                "errors": self.callback_errors,
            }
            print(json.dumps(report, indent=2))
            if report["gateway_messages_lost"] or self.callback_errors:
                raise RuntimeError("MQTT load test did not deliver every message")
            return report
        finally:
            for client in clients:
                client.disconnect()
                client.loop_stop()
            self.observer.disconnect()
            self.observer.loop_stop()
            if self.robot_ids:
                self.redis.hdel(REDIS_ROBOTS, *self.robot_ids)
            self.redis.close()


def _latency_summary(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"samples": 0, "p50": None, "p95": None, "p99": None, "max": None}
    ordered = sorted(values)

    def percentile(value: float) -> float:
        index = max(0, math.ceil(value * len(ordered)) - 1)
        return round(ordered[index], 3)

    return {
        "samples": len(ordered),
        "p50": percentile(0.50),
        "p95": percentile(0.95),
        "p99": percentile(0.99),
        "max": round(ordered[-1], 3),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark MQTT telemetry delivery through the fleet services"
    )
    parser.add_argument("--amrs", type=int, default=DEFAULT_AMR_COUNT)
    parser.add_argument(
        "--messages-per-amr", type=int, default=DEFAULT_MESSAGES_PER_AMR
    )
    parser.add_argument("--interval-ms", type=int, default=DEFAULT_INTERVAL_MS)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    args = parser.parse_args()
    LoadTest(args.amrs, args.messages_per_amr, args.interval_ms, args.timeout).run()


if __name__ == "__main__":
    main()
