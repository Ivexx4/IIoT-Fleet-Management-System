import pytest

from amr_robot import AMRRobot
from models import RobotTelemetry
from task_allocator import (
    plan_grid_route,
    plan_multi_agent_routes,
    route_cost,
    segment_distance_at_time,
    segment_travel_time,
    turn_angle,
    turn_time,
)


def test_route_planner_detours_around_blocked_cell() -> None:
    route = plan_grid_route((4, 5), (6, 5))

    assert route[0] == (4, 5)
    assert route[-1] == (6, 5)
    assert (5, 5) not in route
    assert len(route) - 1 == 4


def test_route_planner_rejects_blocked_endpoints() -> None:
    with pytest.raises(ValueError, match="blocked cells"):
        plan_grid_route((4, 5), (5, 5))


def test_cbs_routes_agents_without_vertex_or_edge_conflicts() -> None:
    routes = plan_multi_agent_routes(
        {"AMR-1": (0, 1), "AMR-2": (2, 1)},
        {"AMR-1": ((2, 1),), "AMR-2": ((0, 1),)},
        bounds=(0, 0, 2, 2),
    )

    assert routes["AMR-1"][0] == (0, 1)
    assert routes["AMR-1"][-1] == (2, 1)
    assert routes["AMR-2"][0] == (2, 1)
    assert routes["AMR-2"][-1] == (0, 1)
    horizon = max(map(len, routes.values()))
    for tick in range(horizon):
        first = routes["AMR-1"][min(tick, len(routes["AMR-1"]) - 1)]
        second = routes["AMR-2"][min(tick, len(routes["AMR-2"]) - 1)]
        assert first != second
        if tick + 1 < horizon:
            first_next = routes["AMR-1"][min(tick + 1, len(routes["AMR-1"]) - 1)]
            second_next = routes["AMR-2"][min(tick + 1, len(routes["AMR-2"]) - 1)]
            assert (first, first_next) != (second_next, second)


def test_cbs_preserves_waypoint_dwell_for_following_leg() -> None:
    route = plan_multi_agent_routes(
        {"AMR-1": (0, 0)},
        {"AMR-1": ((1, 0), (2, 0))},
        bounds=(0, 0, 2, 1),
        waypoint_dwell_steps=2,
    )["AMR-1"]

    shelf_index = route.index((1, 0))
    assert route[shelf_index : shelf_index + 3] == [(1, 0)] * 3
    assert route[-1] == (2, 0)


def test_cbs_rejects_impossible_corridor_swap() -> None:
    with pytest.raises(ValueError, match="CBS"):
        plan_multi_agent_routes(
            {"AMR-1": (0, 0), "AMR-2": (2, 0)},
            {"AMR-1": ((2, 0),), "AMR-2": ((0, 0),)},
            bounds=(0, 0, 2, 0),
            max_high_level_nodes=100,
        )


def test_segment_motion_accelerates_and_brakes() -> None:
    duration = segment_travel_time(1.0, max_speed=1.5, acceleration=1.0)

    assert duration == pytest.approx(2.0)
    assert segment_distance_at_time(0.5, 1.0) == pytest.approx(0.125)
    assert segment_distance_at_time(duration / 2, 1.0) == pytest.approx(0.5)
    assert segment_distance_at_time(duration, 1.0) == pytest.approx(1.0)


def test_turn_kinematics_distinguish_right_angle_and_uturn() -> None:
    assert turn_angle((1, 0), (0, 1)) == 90
    assert turn_angle((1, 0), (-1, 0)) == 180
    assert turn_time(90) == pytest.approx(0.8)
    assert turn_time(180) == pytest.approx(1.5)


def test_route_cost_accounts_for_turns_and_live_robot_position() -> None:
    assert route_cost((0, 0), "S1", "P1") > 9 * segment_travel_time(1.0)


def test_robot_telemetry_accepts_continuous_position_and_cell_reservation() -> None:
    telemetry = RobotTelemetry(
        robot_id="RBT1",
        battery=80,
        status="MOVING_TO_PICK",
        x=2.35,
        y=1.0,
        next_cell_x=3,
        next_cell_y=1,
    )

    assert telemetry.x == 2.35
    assert telemetry.next_cell_x == 3


def test_robot_waits_for_occupied_cell_then_resumes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [100.0]
    monkeypatch.setattr("amr_robot.time.monotonic", lambda: clock[0])
    robot = AMRRobot("test", "RBT2")
    monkeypatch.setattr(robot, "publish_status", lambda: None)
    robot.status = "MOVING_TO_PICK"
    robot.movement_status = "MOVING_TO_PICK"
    robot.movement_target = "SHELF-S1"
    robot.route = [(0, 0), (1, 0)]
    robot.route_index = 1
    robot.segment_start = (0, 0)
    robot.x = 0.0
    robot.y = 0.0
    robot.last_battery_update = 99.9
    robot.peer_robots["RBT1"] = (
        RobotTelemetry(
            robot_id="RBT1",
            battery=90,
            status="IDLE",
            x=1,
            y=0,
        ),
        clock[0],
    )

    robot.update_logic()

    assert robot.status == "STALLED_TRAFFIC"
    assert (robot.x, robot.y) == (0.0, 0.0)

    robot.peer_robots.clear()
    clock[0] += 0.1
    robot.update_logic()

    assert robot.status == "MOVING_TO_PICK"
    assert robot.x > 0.0
