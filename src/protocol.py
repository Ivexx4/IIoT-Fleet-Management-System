import re
import struct
from typing import Final

EXECUTE_TASK: Final[int] = 0x01
FORCE_CHARGE: Final[int] = 0x03
COMMAND_FORMAT: Final[str] = "!BBB"


def _asset_number(value: str | int, prefix: str) -> int:
    if isinstance(value, int):
        number = value
    else:
        match = re.search(r"\d+", value)
        if match is None or not value.startswith(prefix):
            raise ValueError(f"Expected an identifier such as {prefix}1, got {value!r}")
        number = int(match.group())
    if not 0 <= number <= 255:
        raise ValueError("Binary command identifiers must be between 0 and 255")
    return number


def encode_command(
    command: str, shelf_id: str | int = 0, station_id: str | int = 0
) -> bytes:
    if command == "FORCE_CHARGE":
        return struct.pack(COMMAND_FORMAT, FORCE_CHARGE, 0, 0)
    if command != "EXECUTE_TASK":
        raise ValueError(f"Unknown command type: {command}")
    return struct.pack(
        COMMAND_FORMAT,
        EXECUTE_TASK,
        _asset_number(shelf_id, "S"),
        _asset_number(station_id, "P"),
    )


def decode_command(payload: bytes) -> tuple[int, int, int]:
    if len(payload) != struct.calcsize(COMMAND_FORMAT):
        raise ValueError("Control commands must contain exactly 3 bytes")
    return struct.unpack(COMMAND_FORMAT, payload)


def normalize_stock(zone_id: str, raw_stock: float) -> tuple[int, str]:
    if raw_stock < 0:
        raise ValueError("Stock cannot be negative")
    if zone_id == "storage-b":
        return int(raw_stock / 23), "units (converted)"
    return int(raw_stock), "units"
