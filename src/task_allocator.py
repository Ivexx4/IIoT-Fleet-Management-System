from collections.abc import Sequence
from dataclasses import dataclass
from heapq import heappop, heappush
from itertools import count
from math import inf


@dataclass(frozen=True)
class AllocationOrder:
    order_id: str
    shelf_id: str
    station_id: str


@dataclass(frozen=True)
class AllocationRobot:
    robot_id: str
    location: str


_LOCATION_COORDINATES = {
    "DOCK": (0, 0),
    "CHARGING_STATION": (0, 0),
    "P1": (0, 5),
    "P2": (10, 5),
}
WAREHOUSE_OBSTACLES = frozenset({(5, 5)})
GridPoint = tuple[int, int]


def location_coordinates(location: str) -> tuple[int, int]:
    if location in _LOCATION_COORDINATES:
        return _LOCATION_COORDINATES[location]
    normalized = location.removeprefix("SHELF-")
    if normalized.startswith("S") and normalized[1:].isdigit():
        shelf_number = int(normalized[1:])
        if shelf_number <= 5:
            return (shelf_number * 2, 0)
        return ((shelf_number - 5) * 2, 10)
    if location.startswith("STATION-P"):
        station = location.removeprefix("STATION-")
        return _LOCATION_COORDINATES.get(station, (0, 5))
    return (0, 0)


def plan_grid_route(
    start: GridPoint,
    goal: GridPoint,
    obstacles: frozenset[GridPoint] = WAREHOUSE_OBSTACLES,
) -> list[GridPoint]:
    """Find a shortest four-direction route around blocked warehouse cells."""
    if start in obstacles or goal in obstacles:
        raise ValueError("Route endpoints cannot occupy blocked cells")
    if start == goal:
        return [start]

    def heuristic(point: GridPoint) -> int:
        return abs(point[0] - goal[0]) + abs(point[1] - goal[1])

    frontier: list[tuple[int, int, GridPoint]] = []
    heappush(frontier, (heuristic(start), 0, start))
    came_from: dict[GridPoint, GridPoint] = {}
    cost_so_far = {start: 0}
    directions = ((1, 0), (0, 1), (-1, 0), (0, -1))

    while frontier:
        _, current_cost, current = heappop(frontier)
        if current == goal:
            route = [goal]
            while route[-1] != start:
                route.append(came_from[route[-1]])
            return list(reversed(route))
        if current_cost != cost_so_far[current]:
            continue

        for dx, dy in directions:
            neighbor = (current[0] + dx, current[1] + dy)
            if neighbor in obstacles:
                continue
            next_cost = current_cost + 1
            if next_cost >= cost_so_far.get(neighbor, 1_000_000_000):
                continue
            cost_so_far[neighbor] = next_cost
            came_from[neighbor] = current
            heappush(
                frontier,
                (next_cost + heuristic(neighbor), next_cost, neighbor),
            )
    raise ValueError(f"No route found from {start} to {goal}")


def turn_angle(incoming: GridPoint, outgoing: GridPoint) -> int:
    """Return the signed-free turn angle between two cardinal headings."""
    dot = incoming[0] * outgoing[0] + incoming[1] * outgoing[1]
    cross = incoming[0] * outgoing[1] - incoming[1] * outgoing[0]
    if dot == -1:
        return 180
    if cross:
        return 90
    return 0


def segment_travel_time(
    distance: float,
    max_speed: float = 1.5,
    acceleration: float = 1.0,
) -> float:
    """Compute travel time for a segment with linear acceleration and braking."""
    if distance < 0 or max_speed <= 0 or acceleration <= 0:
        raise ValueError("Distance must be non-negative and motion limits positive")
    if distance == 0:
        return 0.0
    acceleration_distance = max_speed**2 / acceleration
    if distance <= acceleration_distance:
        return 2 * (distance / acceleration) ** 0.5
    acceleration_time = max_speed / acceleration
    cruise_time = (distance - acceleration_distance) / max_speed
    return 2 * acceleration_time + cruise_time


def turn_time(angle: int) -> float:
    if angle == 90:
        return 0.8
    if angle == 180:
        return 1.5
    if angle == 0:
        return 0.0
    raise ValueError("Turn angle must be 0, 90, or 180 degrees")


def segment_distance_at_time(
    elapsed: float,
    distance: float,
    max_speed: float = 1.5,
    acceleration: float = 1.0,
) -> float:
    """Return position on a rest-to-rest segment under a trapezoidal speed profile."""
    duration = segment_travel_time(distance, max_speed, acceleration)
    elapsed = min(max(elapsed, 0.0), duration)
    if distance == 0 or elapsed == duration:
        return distance
    acceleration_time = min(max_speed / acceleration, (distance / acceleration) ** 0.5)
    acceleration_distance = 0.5 * acceleration * acceleration_time**2
    if elapsed < acceleration_time:
        return 0.5 * acceleration * elapsed**2
    if distance <= max_speed**2 / acceleration:
        return min(
            distance,
            acceleration_distance
            + max_speed * (elapsed - acceleration_time)
            - 0.5 * acceleration * (elapsed - acceleration_time) ** 2,
        )
    cruise_time = (distance - 2 * acceleration_distance) / max_speed
    if elapsed < acceleration_time + cruise_time:
        return acceleration_distance + max_speed * (elapsed - acceleration_time)
    braking_elapsed = elapsed - acceleration_time - cruise_time
    return distance - 0.5 * acceleration * (acceleration_time - braking_elapsed) ** 2


def route_cost(
    robot_location: str | GridPoint,
    shelf_id: str,
    station_id: str,
) -> float:
    """Estimate motion and turn time on the demo warehouse grid."""
    origin = (
        location_coordinates(robot_location)
        if isinstance(robot_location, str)
        else robot_location
    )
    shelf = location_coordinates(f"SHELF-{shelf_id}")
    station = location_coordinates(station_id)
    first_leg = plan_grid_route(origin, shelf)
    second_leg = plan_grid_route(shelf, station)
    route = first_leg + second_leg[1:]
    travel_time = (len(route) - 1) * segment_travel_time(1.0)
    turn_delay = sum(
        turn_time(
            turn_angle(
                (current[0] - previous[0], current[1] - previous[1]),
                (following[0] - current[0], following[1] - current[1]),
            )
        )
        for previous, current, following in zip(
            route,
            route[1:],
            route[2:],
            strict=False,
        )
    )
    return travel_time + turn_delay


def minimum_cost_assignment(
    costs: Sequence[Sequence[int | float]],
) -> list[tuple[int, int]]:
    """Return a globally minimum one-to-one assignment via the Hungarian method."""
    if not costs or not costs[0]:
        return []
    if any(len(row) != len(costs[0]) for row in costs):
        raise ValueError("Cost matrix must be rectangular")
    if any(cost < 0 for row in costs for cost in row):
        raise ValueError("Assignment costs must be non-negative")

    row_count = len(costs)
    column_count = len(costs[0])
    transposed = row_count > column_count
    matrix = [list(row) for row in zip(*costs, strict=True)] if transposed else costs
    rows = len(matrix)
    columns = len(matrix[0])
    u = [0.0] * (rows + 1)
    v = [0.0] * (columns + 1)
    matching = [0] * (columns + 1)
    previous = [0] * (columns + 1)

    for row in range(1, rows + 1):
        matching[0] = row
        current_column = 0
        minimum = [inf] * (columns + 1)
        used = [False] * (columns + 1)
        while True:
            used[current_column] = True
            current_row = matching[current_column]
            delta = inf
            next_column = 0
            for column in range(1, columns + 1):
                if used[column]:
                    continue
                reduced_cost = (
                    matrix[current_row - 1][column - 1] - u[current_row] - v[column]
                )
                if reduced_cost < minimum[column]:
                    minimum[column] = reduced_cost
                    previous[column] = current_column
                if minimum[column] < delta:
                    delta = minimum[column]
                    next_column = column
            for column in range(columns + 1):
                if used[column]:
                    u[matching[column]] += delta
                    v[column] -= delta
                else:
                    minimum[column] -= delta
            current_column = next_column
            if matching[current_column] == 0:
                break
        while True:
            next_column = previous[current_column]
            matching[current_column] = matching[next_column]
            current_column = next_column
            if current_column == 0:
                break

    pairs = [
        (matching[column] - 1, column - 1)
        for column in range(1, columns + 1)
        if matching[column] != 0
    ]
    return [(column, row) for row, column in pairs] if transposed else pairs


def plan_multi_agent_routes(
    starts: dict[str, GridPoint],
    waypoints: dict[str, Sequence[GridPoint]],
    obstacles: frozenset[GridPoint] = WAREHOUSE_OBSTACLES,
    bounds: tuple[int, int, int, int] = (0, 0, 10, 10),
    waypoint_dwell_steps: int = 0,
    max_high_level_nodes: int = 10_000,
) -> dict[str, list[GridPoint]]:
    """Find conflict-free discrete-time routes with Conflict-Based Search."""
    if starts.keys() != waypoints.keys():
        raise ValueError("Every agent must have a start and at least one waypoint")
    if waypoint_dwell_steps < 0:
        raise ValueError("Waypoint dwell steps must be non-negative")
    if max_high_level_nodes < 1:
        raise ValueError("CBS node limit must be positive")

    min_x, min_y, max_x, max_y = bounds
    if min_x > max_x or min_y > max_y:
        raise ValueError("Grid bounds are invalid")

    agents = sorted(starts)
    if any(not waypoints[agent] for agent in agents):
        raise ValueError("Every agent must have at least one waypoint")
    locations = [starts[agent] for agent in agents]
    if len(set(locations)) != len(locations):
        raise ValueError("Agents cannot share a start cell")
    for agent in agents:
        points = (starts[agent], *waypoints[agent])
        if any(
            point in obstacles
            or not min_x <= point[0] <= max_x
            or not min_y <= point[1] <= max_y
            for point in points
        ):
            raise ValueError(f"Agent {agent} has a waypoint outside the free grid")

    vertex_constraints: dict[str, set[tuple[GridPoint, int]]] = {
        agent: set() for agent in agents
    }
    edge_constraints: dict[str, set[tuple[GridPoint, GridPoint, int]]] = {
        agent: set() for agent in agents
    }
    cell_count = (max_x - min_x + 1) * (max_y - min_y + 1)
    max_time = cell_count * max(2, len(agents)) * 2
    directions = ((1, 0), (0, 1), (-1, 0), (0, -1), (0, 0))

    def low_level_search(
        agent: str,
        forbidden_vertices: set[tuple[GridPoint, int]],
        forbidden_edges: set[tuple[GridPoint, GridPoint, int]],
    ) -> list[GridPoint]:
        goals = waypoints[agent]
        start = starts[agent]
        stage = 0
        dwell = 0
        if start == goals[0]:
            stage = 1
            dwell = waypoint_dwell_steps

        initial = (start, 0, stage, dwell)
        frontier: list[tuple[int, int, int, GridPoint, int, int]] = []
        heappush(
            frontier,
            (
                sum(abs(start[axis] - goals[0][axis]) for axis in (0, 1)),
                0,
                0,
                start,
                stage,
                dwell,
            ),
        )
        parents: dict[
            tuple[GridPoint, int, int, int],
            tuple[GridPoint, int, int, int] | None,
        ] = {initial: None}

        while frontier:
            _, elapsed, _, current, current_stage, current_dwell = heappop(frontier)
            state = (current, elapsed, current_stage, current_dwell)
            if (
                current_stage == len(goals)
                and current_dwell == 0
                and current == goals[-1]
                and all(
                    (goals[-1], future_time) not in forbidden_vertices
                    and (goals[-1], goals[-1], future_time) not in forbidden_edges
                    for future_time in range(elapsed + 1, max_time + 1)
                )
            ):
                path: list[GridPoint] = []
                cursor: tuple[GridPoint, int, int, int] | None = state
                while cursor is not None:
                    path.append(cursor[0])
                    cursor = parents[cursor]
                return list(reversed(path))
            if elapsed >= max_time:
                continue

            next_time = elapsed + 1
            moves = (
                (current,)
                if current_dwell
                else ((current[0] + dx, current[1] + dy) for dx, dy in directions)
            )
            for neighbor in moves:
                if neighbor in obstacles or not (
                    min_x <= neighbor[0] <= max_x and min_y <= neighbor[1] <= max_y
                ):
                    continue
                if (neighbor, next_time) in forbidden_vertices:
                    continue
                if (current, neighbor, elapsed) in forbidden_edges:
                    continue

                next_stage = current_stage
                next_dwell = max(0, current_dwell - 1)
                if not current_dwell and next_stage < len(goals):
                    if neighbor == goals[next_stage]:
                        next_stage += 1
                        next_dwell = waypoint_dwell_steps
                next_state = (neighbor, next_time, next_stage, next_dwell)
                if next_state in parents:
                    continue
                parents[next_state] = state

                remaining = 0
                estimate_from = neighbor
                for goal in goals[next_stage:]:
                    remaining += abs(estimate_from[0] - goal[0]) + abs(
                        estimate_from[1] - goal[1]
                    )
                    estimate_from = goal
                remaining += (
                    next_dwell
                    + max(0, len(goals) - next_stage - 1) * waypoint_dwell_steps
                )
                heappush(
                    frontier,
                    (
                        next_time + remaining,
                        next_time,
                        len(parents),
                        neighbor,
                        next_stage,
                        next_dwell,
                    ),
                )
        raise ValueError(f"CBS found no route for agent {agent}")

    def find_conflict(
        routes: dict[str, list[GridPoint]],
    ) -> tuple[str, str, str, int, GridPoint, GridPoint | None] | None:
        route_agents = sorted(routes)
        horizon = max(map(len, routes.values()))

        def position(agent: str, tick: int) -> GridPoint:
            route = routes[agent]
            return route[min(tick, len(route) - 1)]

        for tick in range(horizon):
            for first_index, first in enumerate(route_agents):
                for second in route_agents[first_index + 1 :]:
                    first_cell = position(first, tick)
                    second_cell = position(second, tick)
                    if first_cell == second_cell:
                        return ("vertex", first, second, tick, first_cell, None)
                    if tick + 1 >= horizon:
                        continue
                    first_next = position(first, tick + 1)
                    second_next = position(second, tick + 1)
                    if first_cell == second_next and second_cell == first_next:
                        return (
                            "edge",
                            first,
                            second,
                            tick,
                            first_cell,
                            first_next,
                        )
        return None

    initial_routes = {agent: low_level_search(agent, set(), set()) for agent in agents}
    queue: list[
        tuple[
            int,
            int,
            dict[str, list[GridPoint]],
            dict[str, set[tuple[GridPoint, int]]],
            dict[str, set[tuple[GridPoint, GridPoint, int]]],
        ]
    ] = []
    tie_breaker = count()

    def enqueue(
        routes: dict[str, list[GridPoint]],
        vertices: dict[str, set[tuple[GridPoint, int]]],
        edges: dict[str, set[tuple[GridPoint, GridPoint, int]]],
    ) -> None:
        total_cost = sum(len(route) - 1 for route in routes.values())
        heappush(
            queue,
            (total_cost, next(tie_breaker), routes, vertices, edges),
        )

    enqueue(initial_routes, vertex_constraints, edge_constraints)
    expanded = 0
    while queue and expanded < max_high_level_nodes:
        _, _, routes, vertices, edges = heappop(queue)
        expanded += 1
        conflict = find_conflict(routes)
        if conflict is None:
            return routes

        kind, first, second, tick, cell, next_cell = conflict
        for agent in (first, second):
            next_vertices = {name: set(value) for name, value in vertices.items()}
            next_edges = {name: set(value) for name, value in edges.items()}
            if kind == "vertex":
                next_vertices[agent].add((cell, tick))
            else:
                assert next_cell is not None
                route = routes[agent]
                current = route[min(tick, len(route) - 1)]
                following = route[min(tick + 1, len(route) - 1)]
                next_edges[agent].add((current, following, tick))
            try:
                replacement = low_level_search(
                    agent, next_vertices[agent], next_edges[agent]
                )
            except ValueError:
                continue
            next_routes = dict(routes)
            next_routes[agent] = replacement
            enqueue(next_routes, next_vertices, next_edges)

    raise ValueError("CBS could not find conflict-free routes within its search limit")
