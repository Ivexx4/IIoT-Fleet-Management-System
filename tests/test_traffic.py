import pytest

from amr_robot import PEER_STATE_TIMEOUT, AMRRobot
from models import RobotTelemetry
from task_allocator import plan_multi_agent_routes


def _crossing_routes() -> dict[str, list[tuple[int, int]]]:
    return plan_multi_agent_routes(
        {
            "AMR-1": (0, 0),
            "AMR-2": (3, 0),
            "AMR-3": (0, 3),
            "AMR-4": (3, 3),
        },
        {
            "AMR-1": ((3, 3),),
            "AMR-2": ((0, 3),),
            "AMR-3": ((3, 0),),
            "AMR-4": ((0, 0),),
        },
        bounds=(0, 0, 3, 3),
    )


def test_cbs_routes_the_four_amr_demo_fleet_without_traffic_conflicts() -> None:
    routes = _crossing_routes()
    horizon = max(map(len, routes.values()))

    assert set(routes) == {"AMR-1", "AMR-2", "AMR-3", "AMR-4"}
    for tick in range(horizon):
        positions = [route[min(tick, len(route) - 1)] for route in routes.values()]
        assert len(positions) == len(set(positions)), f"vertex conflict at tick {tick}"

        if tick + 1 == horizon:
            continue
        transitions = [
            (
                route[min(tick, len(route) - 1)],
                route[min(tick + 1, len(route) - 1)],
            )
            for route in routes.values()
        ]
        for index, (start, end) in enumerate(transitions):
            assert (end, start) not in transitions[index + 1 :], (
                f"head-on edge conflict at tick {tick}"
            )


def test_cbs_search_limit_is_enforced_for_conflicting_routes() -> None:
    with pytest.raises(ValueError, match="search limit"):
        plan_multi_agent_routes(
            {"AMR-1": (0, 1), "AMR-2": (2, 1)},
            {"AMR-1": ((2, 1),), "AMR-2": ((0, 1),)},
            bounds=(0, 0, 2, 2),
            max_high_level_nodes=1,
        )


@pytest.mark.parametrize(
    ("peer_id", "age", "expected_blocker"),
    [
        ("RBT1", PEER_STATE_TIMEOUT, "RBT1"),
        ("RBT1", PEER_STATE_TIMEOUT + 0.001, None),
        ("RBT3", PEER_STATE_TIMEOUT, None),
    ],
)
def test_traffic_reservations_follow_priority_and_two_second_peer_timeout(
    monkeypatch: pytest.MonkeyPatch,
    peer_id: str,
    age: float,
    expected_blocker: str | None,
) -> None:
    now = 100.0
    monkeypatch.setattr("amr_robot.time.monotonic", lambda: now)
    robot = AMRRobot("test", "RBT2")
    robot.peer_robots[peer_id] = (
        RobotTelemetry(
            robot_id=peer_id,
            battery=90,
            status="MOVING_TO_PICK",
            x=3,
            y=3,
            next_cell_x=1,
            next_cell_y=0,
        ),
        now - age,
    )

    assert robot._traffic_blocker((1, 0)) == expected_blocker


def test_peer_timeout_is_two_seconds() -> None:
    assert PEER_STATE_TIMEOUT == 2.0
