import pytest

from models import Order, RobotTelemetry
from protocol import decode_command, encode_command, normalize_stock
from task_allocator import minimum_cost_assignment, route_cost, segment_travel_time


def test_task_command_binary_round_trip() -> None:
    payload = encode_command("EXECUTE_TASK", "S6", "P2", 7)
    assert len(payload) == 4
    assert decode_command(payload) == (0x01, 6, 2, 7)


def test_force_charge_command_binary_round_trip() -> None:
    assert decode_command(encode_command("FORCE_CHARGE")) == (0x03, 0, 0, 0)


@pytest.mark.parametrize(
    ("command", "shelf", "station", "quantity"),
    [
        ("UNKNOWN", "S1", "P1", 1),
        ("EXECUTE_TASK", "not-a-shelf", "P1", 1),
        ("EXECUTE_TASK", "S256", "P1", 1),
        ("EXECUTE_TASK", "S1", "P256", 1),
        ("EXECUTE_TASK", "S1", "P1", 0),
        ("EXECUTE_TASK", "S1", "P1", 256),
    ],
)
def test_rejects_invalid_command_identifiers(
    command: str,
    shelf: str,
    station: str,
    quantity: int,
) -> None:
    with pytest.raises(ValueError):
        encode_command(command, shelf, station, quantity)


def test_rejects_wrong_command_size() -> None:
    with pytest.raises(ValueError, match="exactly 4 bytes"):
        decode_command(b"\x01\x02")


def test_normalizes_storage_b_kilograms_to_units() -> None:
    assert normalize_stock("storage-b", 46) == (2, "units (converted)")
    assert normalize_stock("storage-a", 46) == (46, "units")


def test_rejects_negative_stock() -> None:
    with pytest.raises(ValueError, match="negative"):
        normalize_stock("storage-a", -1)


def test_hungarian_assignment_minimizes_global_cost() -> None:
    costs = [[4, 1], [2, 0], [3, 2]]
    assignments = minimum_cost_assignment(costs)
    assert len(assignments) == 2
    assert sum(costs[row][column] for row, column in assignments) == 3


def test_hungarian_assignment_handles_more_orders_than_robots() -> None:
    costs = [[7, 2, 5], [3, 8, 1]]
    assignments = minimum_cost_assignment(costs)
    assert len(assignments) == 2
    assert sum(costs[row][column] for row, column in assignments) == 3


def test_hungarian_assignment_rejects_invalid_matrix() -> None:
    with pytest.raises(ValueError, match="rectangular"):
        minimum_cost_assignment([[1, 2], [3]])
    with pytest.raises(ValueError, match="non-negative"):
        minimum_cost_assignment([[-1]])


def test_route_estimate_includes_shelf_to_station_leg() -> None:
    assert route_cost("DOCK", "S1", "P1") > 9 * segment_travel_time(1.0)


def test_message_models_validate_external_payloads() -> None:
    assert Order(item="item_A", quantity=5).pack_station == "P1"
    with pytest.raises(ValueError):
        Order(item="item_A", quantity=0)
    with pytest.raises(ValueError):
        Order(item="item_A", quantity=256)
    with pytest.raises(ValueError):
        RobotTelemetry(robot_id="R1", battery=101, status="IDLE")
