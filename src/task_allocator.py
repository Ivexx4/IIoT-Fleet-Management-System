from dataclasses import dataclass
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


def route_cost(robot_location: str, shelf_id: str, station_id: str) -> int:
    """Estimate route length on the documented demo warehouse grid."""
    origin = location_coordinates(robot_location)
    shelf = location_coordinates(f"SHELF-{shelf_id}")
    station = location_coordinates(station_id)
    return (
        abs(origin[0] - shelf[0])
        + abs(origin[1] - shelf[1])
        + abs(shelf[0] - station[0])
        + abs(shelf[1] - station[1])
    )


def minimum_cost_assignment(
    costs: list[list[int | float]],
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
                    matrix[current_row - 1][column - 1]
                    - u[current_row]
                    - v[column]
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
