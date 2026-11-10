from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class MessageModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RobotTelemetry(MessageModel):
    robot_id: str = Field(min_length=1)
    timestamp: str | None = None
    location_id: str = "UNKNOWN"
    battery: float = Field(ge=0, le=100)
    status: str
    load_quantity: int = Field(default=0, ge=0, le=255)
    x: float | None = None
    y: float | None = None
    next_cell_x: int | None = None
    next_cell_y: int | None = None


class ShelfTelemetry(MessageModel):
    asset_id: str = Field(alias="asset id", min_length=1)
    item_id: str = Field(alias="item id", min_length=1)
    type: Literal["SHELF"] = "SHELF"
    zone_id: str = Field(default="UNKNOWN", min_length=1)
    location_id: str = Field(default="UNKNOWN", min_length=1)
    x: float | None = None
    y: float | None = None
    timestamp: str | None = None
    stock: float = Field(ge=0)
    unit: str = "units"
    stock_units: int | None = Field(default=None, ge=0)


class StationTelemetry(MessageModel):
    station_id: str = Field(min_length=1)
    location_id: str = Field(default="UNKNOWN", min_length=1)
    x: float | None = None
    y: float | None = None
    timestamp: str | None = None
    status: str
    time_remaining_s: float = Field(default=0, ge=0)
    delivery_info: dict[str, Any] | None = None


class Order(MessageModel):
    order_id: str | None = None
    item: str = Field(min_length=1)
    quantity: int = Field(default=1, gt=0, le=255)
    pack_station: str = Field(default="P1", min_length=1)


class DispatchCommand(MessageModel):
    robot_id: str = Field(min_length=1)
    command: Literal["EXECUTE_TASK", "FORCE_CHARGE"]
    target_shelf_id: str | int = 0
    target_station_id: str | int = 0
    quantity: int = Field(default=1, ge=1, le=255)


class HealthAlert(MessageModel):
    alert_id: str = Field(min_length=1)
    robot_id: str = Field(min_length=1)
    level: Literal["CRITICAL", "WARNING"] = "CRITICAL"
    message: str = Field(min_length=1)
    override_task: Literal["FORCE_CHARGE"] | None = None


class InfrastructureLocation(MessageModel):
    asset_type: Literal["shelf", "station"]
    asset_id: str = Field(min_length=1)
    location_id: str = Field(min_length=1)
    x: float
    y: float
    zone_id: str | None = None


class LocationRequest(MessageModel):
    request_id: str = Field(min_length=1)
