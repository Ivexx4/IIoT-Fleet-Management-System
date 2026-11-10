import json
import threading
from types import SimpleNamespace
from typing import Any, cast

import paho.mqtt.client as mqtt

from fleet_coordinator import (
    GROUP_ID,
    REDIS_ACTIVE_TASKS,
    REDIS_PENDING_ORDER_IDS,
    REDIS_PENDING_ORDERS,
    REDIS_RESERVED_ROBOTS,
    REDIS_ROBOTS,
    REQUEUE_OFFLINE_TASK_SCRIPT,
    FleetCoordinator,
)
from warehouse_gateway import WarehouseGateway


class FailoverRedis:
    def __init__(self) -> None:
        self.robot_state = json.dumps({"availability": "online"})
        self.writes: list[tuple[str, str, str]] = []
        self.eval_calls: list[tuple[str, int, tuple[str, ...]]] = []

    def hget(self, name: str, key: str) -> str | None:
        assert name == REDIS_ROBOTS
        assert key == "AMR-1"
        return self.robot_state

    def hgetall(self, name: str) -> dict[str, str]:
        assert name == REDIS_ROBOTS
        return {"AMR-1": self.robot_state}

    def hset(self, name: str, key: str, value: str) -> int:
        self.writes.append((name, key, value))
        self.robot_state = value
        return 1

    def eval(self, script: str, numkeys: int, *args: str) -> int:
        self.eval_calls.append((script, numkeys, args))
        return 1


def _coordinator(redis: FailoverRedis) -> FleetCoordinator:
    coordinator = FleetCoordinator.__new__(FleetCoordinator)
    coordinator.redis = cast(Any, redis)
    coordinator.lock = threading.Lock()
    coordinator.robot_last_seen = {}
    coordinator.robot_heartbeat_timeout = 5.0
    return coordinator


def _availability_message(robot_id: str, payload_id: str) -> mqtt.MQTTMessage:
    return cast(
        mqtt.MQTTMessage,
        SimpleNamespace(
            topic=f"{GROUP_ID}/internal/amr/{robot_id}/availability",
            payload=json.dumps({"robot_id": payload_id, "status": "offline"}).encode(),
        ),
    )


def test_offline_lwt_marks_amr_unavailable_and_requeues_its_active_task() -> None:
    redis = FailoverRedis()
    coordinator = _coordinator(redis)

    assert coordinator.on_internal_message(
        cast(mqtt.Client, object()),
        None,
        _availability_message("AMR-1", "AMR-1"),
    )

    assert json.loads(redis.robot_state)["availability"] == "offline"
    assert redis.eval_calls == [
        (
            REQUEUE_OFFLINE_TASK_SCRIPT,
            4,
            (
                REDIS_ACTIVE_TASKS,
                REDIS_PENDING_ORDERS,
                REDIS_RESERVED_ROBOTS,
                REDIS_PENDING_ORDER_IDS,
                "AMR-1",
            ),
        )
    ]


def test_availability_payload_cannot_impersonate_another_amr() -> None:
    redis = FailoverRedis()
    coordinator = _coordinator(redis)

    assert coordinator.on_internal_message(
        cast(mqtt.Client, object()),
        None,
        _availability_message("AMR-1", "AMR-2"),
    )

    assert json.loads(redis.robot_state)["availability"] == "online"
    assert redis.eval_calls == []


def test_gateway_rejects_telemetry_with_a_mismatched_topic_identity() -> None:
    gateway = WarehouseGateway.__new__(WarehouseGateway)

    try:
        gateway.process_sensor_data(
            f"warehouse/{GROUP_ID}/amr/AMR-1/status",
            json.dumps(
                {
                    "robot_id": "AMR-2",
                    "battery": 80,
                    "status": "IDLE",
                }
            ),
        )
    except ValueError as error:
        assert "must match" in str(error)
    else:
        raise AssertionError("Gateway accepted mismatched AMR identity")


def test_heartbeat_timeout_marks_amr_offline_and_requeues_task() -> None:
    redis = FailoverRedis()
    coordinator = _coordinator(redis)
    coordinator.robot_last_seen["AMR-1"] = 100.0

    timed_out = coordinator.check_robot_timeouts(now=106.0)

    assert timed_out == ["AMR-1"]
    assert json.loads(redis.robot_state)["availability"] == "offline"
    assert redis.eval_calls == [
        (
            REQUEUE_OFFLINE_TASK_SCRIPT,
            4,
            (
                REDIS_ACTIVE_TASKS,
                REDIS_PENDING_ORDERS,
                REDIS_RESERVED_ROBOTS,
                REDIS_PENDING_ORDER_IDS,
                "AMR-1",
            ),
        )
    ]


def test_amr_with_recent_heartbeat_remains_online() -> None:
    redis = FailoverRedis()
    coordinator = _coordinator(redis)
    coordinator.robot_last_seen["AMR-1"] = 100.0

    timed_out = coordinator.check_robot_timeouts(now=103.0)

    assert timed_out == []
    assert json.loads(redis.robot_state)["availability"] == "online"
    assert redis.eval_calls == []
