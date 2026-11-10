from collections.abc import Sequence
from dataclasses import dataclass
from heapq import heappop, heappush
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
