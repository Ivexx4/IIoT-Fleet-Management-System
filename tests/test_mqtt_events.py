import json
import threading
from types import SimpleNamespace
from typing import cast

import paho.mqtt.client as mqtt
import pytest
from influxdb_client.client.write_api import WriteApi
from redis import Redis

import mqtt_config
from fleet_coordinator import FleetCoordinator, RedisState
from models import HealthAlert
from mqtt_config import (
    MQTT_QOS,
    TOPIC_CRITICAL_ALERTS,
    TOPIC_ORDER_ACK,
    TOPIC_ORDER_SUBMIT,
    create_mqtt_client,
)
from system_monitor import SystemMonitor
from warehouse_gateway import REDIS_PROCESSED_ALERTS, WarehouseGateway


class FakeRedis:
    def __init__(self, enqueue_result: int = 1) -> None:
        self.enqueue_result = enqueue_result
        self.enqueued: list[tuple[object, ...]] = []
        self.processed_alerts: set[str] = set()

    def eval(self, script: str, numkeys: int, *keys_and_args: str) -> int:
        del script, numkeys
        self.enqueued.append(keys_and_args)
        return self.enqueue_result

    def sismember(self, name: str, value: str) -> bool:
        assert name == REDIS_PROCESSED_ALERTS
        return value in self.processed_alerts

    def sadd(self, name: str, value: str) -> int:
        assert name == REDIS_PROCESSED_ALERTS
        self.processed_alerts.add(value)
        return 1


class FakeClient:
    def __init__(self) -> None:
        self.published: list[tuple[str, str, int]] = []
        self.acknowledged: list[tuple[int, int]] = []
        self.publish_result = SimpleNamespace(rc=mqtt.MQTT_ERR_SUCCESS)

    def publish(
        self,
        topic: str,
        payload: str,
        *,
        qos: int,
    ) -> SimpleNamespace:
        self.published.append((topic, payload, qos))
        return self.publish_result

    def ack(self, mid: int, qos: int) -> int:
        self.acknowledged.append((mid, qos))
        return mqtt.MQTT_ERR_SUCCESS


def test_coordinator_enqueues_order_and_publishes_application_ack() -> None:
    redis = FakeRedis()
    client = FakeClient()
    coordinator = FleetCoordinator.__new__(FleetCoordinator)
    coordinator.redis = cast(RedisState, redis)
    coordinator.client = cast(mqtt.Client, client)
    order_id = "order-1"
    payload = json.dumps(
        {
            "order_id": order_id,
            "item": "item_A",
            "quantity": 3,
            "pack_station": "P1",
        }
    ).encode()
    message = cast(
        mqtt.MQTTMessage,
        SimpleNamespace(
            topic=TOPIC_ORDER_SUBMIT,
            payload=payload,
            mid=7,
            qos=1,
        ),
    )

    coordinator.on_message(cast(mqtt.Client, client), None, message)

    assert len(redis.enqueued) == 1
    topic, response_payload, qos = client.published[0]
    assert topic == f"{TOPIC_ORDER_ACK}/{order_id}"
    assert json.loads(response_payload) == {
        "order_id": order_id,
        "status": "accepted",
    }
    assert qos == MQTT_QOS
    assert client.acknowledged == [(7, 1)]


def test_coordinator_does_not_ack_order_when_redis_fails() -> None:
    class UnavailableRedis(FakeRedis):
        def eval(self, script: str, numkeys: int, *keys_and_args: str) -> int:
            raise RuntimeError("Redis unavailable")

    client = FakeClient()
    coordinator = FleetCoordinator.__new__(FleetCoordinator)
    coordinator.redis = cast(RedisState, UnavailableRedis())
    coordinator.client = cast(mqtt.Client, client)
    message = cast(
        mqtt.MQTTMessage,
        SimpleNamespace(
            topic=TOPIC_ORDER_SUBMIT,
            payload=b'{"order_id":"order-1","item":"item_A","quantity":1}',
            mid=8,
            qos=1,
        ),
    )

    coordinator.on_message(cast(mqtt.Client, client), None, message)

    assert client.acknowledged == []
    assert client.published == []


def test_gateway_deduplicates_critical_alerts_in_redis() -> None:
    class FakeWriteApi:
        def __init__(self) -> None:
            self.writes = 0

        def write(self, *, bucket: str, record: object) -> None:
            del bucket, record
            self.writes += 1

    redis = FakeRedis()
    write_api = FakeWriteApi()
    gateway = WarehouseGateway.__new__(WarehouseGateway)
    gateway.redis = cast(Redis, redis)
    gateway.write_api = cast(WriteApi, write_api)
    gateway.alert_lock = threading.Lock()
    alert = HealthAlert(
        alert_id="alert-1",
        robot_id="AMR-1",
        message="Critical battery",
    )

    assert gateway.process_alert(alert) == "accepted"
    assert gateway.process_alert(alert) == "duplicate"
    assert write_api.writes == 1


def test_monitor_publishes_critical_alert_with_qos_one() -> None:
    client = FakeClient()
    info = SimpleNamespace(
        rc=mqtt.MQTT_ERR_SUCCESS,
        wait_for_publish=lambda timeout: None,
        is_published=lambda: True,
    )
    client.publish_result = info
    monitor = SystemMonitor.__new__(SystemMonitor)
    monitor.client = cast(mqtt.Client, client)
    alert = HealthAlert(
        alert_id="alert-1",
        robot_id="AMR-1",
        message="Critical battery",
    )

    assert monitor.send_alert(alert)
    topic, payload, qos = client.published[0]
    assert topic == TOPIC_CRITICAL_ALERTS
    assert json.loads(payload)["alert_id"] == alert.alert_id
    assert qos == MQTT_QOS


def test_critical_consumer_client_uses_persistent_manual_ack() -> None:
    client = create_mqtt_client(
        "critical-consumer-test",
        persistent_session=True,
        manual_ack=True,
    )

    assert client._clean_session is False
    assert client._manual_ack is True


@pytest.mark.parametrize("role", ["order", "command"])
def test_injection_roles_require_dedicated_credentials(
    monkeypatch: pytest.MonkeyPatch,
    role: str,
) -> None:
    monkeypatch.setattr(mqtt_config, "MQTT_PASSWORD", "shared-service-password")
    monkeypatch.setattr(mqtt_config, f"MQTT_{role.upper()}_PASSWORD", None)

    with pytest.raises(RuntimeError, match=f"MQTT {role} credentials are required"):
        create_mqtt_client("injection-test", credential_role=role)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("role", "username", "password"),
    [
        ("order", "order-client", "order-secret"),
        ("command", "manual-command", "command-secret"),
    ],
)
def test_injection_roles_use_separate_credentials(
    monkeypatch: pytest.MonkeyPatch,
    role: str,
    username: str,
    password: str,
) -> None:
    monkeypatch.setattr(mqtt_config, "MQTT_USERNAME", "shared-service")
    monkeypatch.setattr(mqtt_config, "MQTT_PASSWORD", "shared-service-password")
    monkeypatch.setattr(mqtt_config, f"MQTT_{role.upper()}_USERNAME", username)
    monkeypatch.setattr(mqtt_config, f"MQTT_{role.upper()}_PASSWORD", password)

    client = create_mqtt_client("injection-test", credential_role=role)  # type: ignore[arg-type]

    assert client._username == username.encode()
    assert client._password == password.encode()
