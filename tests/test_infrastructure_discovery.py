from threading import Lock
from types import SimpleNamespace
from typing import Any

from fleet_coordinator import (
    REDIS_REMOVED_SHELVES,
    REDIS_REMOVED_STATIONS,
    REDIS_SHELVES,
    REDIS_STATIONS,
    TOPIC_LOCATION_REQUEST,
    FleetCoordinator,
)
from loading_station import LoadingStation
from models import InfrastructureLocation, ShelfTelemetry, StationTelemetry
from shelves import Shelf


class MemoryRedis:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.sets: dict[str, set[str]] = {}

    def hget(self, name: str, key: str) -> str | None:
        return self.hashes.get(name, {}).get(key)

    def hset(self, name: str, key: str, value: str) -> int:
        values = self.hashes.setdefault(name, {})
        is_new = key not in values
        values[key] = value
        return int(is_new)

    def hgetall(self, name: str) -> dict[str, str]:
        return dict(self.hashes.get(name, {}))

    def hdel(self, name: str, key: str) -> int:
        return int(self.hashes.get(name, {}).pop(key, None) is not None)

    def sadd(self, name: str, value: str) -> int:
        values = self.sets.setdefault(name, set())
        is_new = value not in values
        values.add(value)
        return int(is_new)

    def srem(self, name: str, value: str) -> int:
        values = self.sets.setdefault(name, set())
        existed = value in values
        values.discard(value)
        return int(existed)

    def sismember(self, name: str, value: str) -> bool:
        return value in self.sets.get(name, set())


class MemoryMqttClient:
    def __init__(self) -> None:
        self.published: list[tuple[str, str, int]] = []

    def publish(
        self,
        topic: str,
        payload: str,
        qos: int,
    ) -> SimpleNamespace:
        self.published.append((topic, payload, qos))
        return SimpleNamespace(rc=0)


def make_coordinator() -> Any:
    coordinator: Any = FleetCoordinator.__new__(FleetCoordinator)
    coordinator.redis = MemoryRedis()
    coordinator.client = MemoryMqttClient()
    coordinator.lock = Lock()
    coordinator.last_discovery_request = {}
    coordinator.discovery_interval = 30.0
    coordinator.discovery_retry_interval = 5.0
    return coordinator


def test_unknown_asset_triggers_a_shared_location_request(
    monkeypatch: Any,
) -> None:
    coordinator = make_coordinator()
    monkeypatch.setattr("fleet_coordinator.time.monotonic", lambda: 10.0)

    coordinator._request_location_if_unknown("shelf", "S12")
    coordinator._request_location_if_unknown("shelf", "S12")

    assert len(coordinator.client.published) == 1
    topic, payload, _ = coordinator.client.published[0]
    assert topic == TOPIC_LOCATION_REQUEST
    assert "request_id" in payload


def test_shelves_and_stations_publish_location_on_the_same_channel(
    monkeypatch: Any,
) -> None:
    shelf_client = MemoryMqttClient()
    station_client = MemoryMqttClient()
    clients = iter((shelf_client, station_client))
    monkeypatch.setattr("shelves.create_mqtt_client", lambda _: next(clients))
    monkeypatch.setattr(
        "loading_station.create_mqtt_client",
        lambda _: next(clients),
    )
    shelf = Shelf("test", "storage-a", "S2", 2)
    station = LoadingStation("test", "P1")

    shelf.publish_location()
    station.publish_location()

    assert shelf_client.published[0][0] == station_client.published[0][0]
    assert (
        InfrastructureLocation.model_validate_json(
            shelf_client.published[0][1]
        ).asset_type
        == "shelf"
    )
    assert (
        InfrastructureLocation.model_validate_json(
            station_client.published[0][1]
        ).asset_type
        == "station"
    )


def test_location_registration_preserves_live_shelf_stock() -> None:
    coordinator = make_coordinator()
    shelf = ShelfTelemetry.model_validate(
        {
            "asset id": "S2",
            "item id": "item_A",
            "zone_id": "storage-a",
            "location_id": "UNKNOWN",
            "stock": 37,
        }
    )
    coordinator.redis.hset(
        REDIS_SHELVES,
        shelf.asset_id,
        shelf.model_dump_json(by_alias=True),
    )

    coordinator._register_location(
        InfrastructureLocation(
            asset_type="shelf",
            asset_id="S2",
            zone_id="storage-a",
            location_id="SHELF-S2",
            x=4,
            y=0,
        )
    )

    registered = ShelfTelemetry.model_validate_json(
        coordinator.redis.hget(REDIS_SHELVES, "S2") or ""
    )
    assert registered.item_id == "item_A"
    assert registered.stock == 37
    assert registered.location_id == "SHELF-S2"
    assert registered.x == 4


def test_removed_assets_are_not_readded_by_discovery() -> None:
    coordinator = make_coordinator()
    coordinator.add_shelf(
        ShelfTelemetry.model_validate(
            {
                "asset id": "S2",
                "item id": "item_A",
                "location_id": "SHELF-S2",
                "x": 4,
                "y": 0,
                "stock": 30,
            }
        )
    )
    coordinator.add_station(
        StationTelemetry(station_id="P1", status="IDLE", location_id="STATION-P1")
    )

    assert coordinator.remove_shelf("S2")
    assert coordinator.remove_station("P1")
    coordinator._register_location(
        InfrastructureLocation(
            asset_type="shelf",
            asset_id="S2",
            location_id="SHELF-S2",
            x=4,
            y=0,
        )
    )
    coordinator._register_location(
        InfrastructureLocation(
            asset_type="station",
            asset_id="P1",
            location_id="STATION-P1",
            x=0,
            y=5,
        )
    )

    assert coordinator.redis.hget(REDIS_SHELVES, "S2") is None
    assert coordinator.redis.hget(REDIS_STATIONS, "P1") is None
    assert coordinator.redis.sismember(REDIS_REMOVED_SHELVES, "S2")
    assert coordinator.redis.sismember(REDIS_REMOVED_STATIONS, "P1")


def test_explicit_add_reactivates_removed_asset() -> None:
    coordinator = make_coordinator()
    coordinator.remove_shelf("S3")

    coordinator.add_shelf(
        ShelfTelemetry.model_validate(
            {
                "asset id": "S3",
                "item id": "item_A",
                "location_id": "SHELF-S3",
                "x": 6,
                "y": 0,
                "stock": 10,
            }
        )
    )

    assert not coordinator.redis.sismember(REDIS_REMOVED_SHELVES, "S3")
    assert coordinator.redis.hget(REDIS_SHELVES, "S3") is not None
