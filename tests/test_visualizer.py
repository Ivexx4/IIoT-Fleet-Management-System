import json
from types import SimpleNamespace
from typing import cast

import paho.mqtt.client as mqtt

from mqtt_config import GROUP_ID
from visualizer import WEB_ROOT, WarehouseState


def test_control_center_contains_live_map_and_embedded_grafana_dashboard() -> None:
    page = (WEB_ROOT / "index.html").read_text(encoding="utf-8")

    assert 'id="map-section"' in page
    assert 'id="metrics"' in page
    assert 'title="Métricas e histórico do armazém no Grafana"' in page
    assert (
        "`${window.location.protocol}//${window.location.hostname}:3000/"
        "d/warehouse-overview/warehouse-overview?orgId=1&refresh=10s`"
    ) in page
    assert 'src="http://localhost:3000' not in page


def test_visualizer_tracks_latest_valid_mqtt_telemetry() -> None:
    state = WarehouseState()
    robot_message = cast(
        mqtt.MQTTMessage,
        SimpleNamespace(
            topic=f"warehouse/{GROUP_ID}/amr/AMR-1/status",
            payload=json.dumps(
                {
                    "robot_id": "AMR-1",
                    "battery": 82,
                    "status": "MOVING_TO_PICK",
                    "x": 2.4,
                    "y": 1.0,
                }
            ).encode(),
        ),
    )
    invalid_message = cast(
        mqtt.MQTTMessage,
        SimpleNamespace(
            topic=f"warehouse/{GROUP_ID}/amr/AMR-2/status",
            payload=b'{"robot_id": "AMR-2", "battery": 150, "status": "IDLE"}',
        ),
    )

    state.on_message(cast(mqtt.Client, None), None, robot_message)
    state.on_message(cast(mqtt.Client, None), None, invalid_message)

    snapshot = state.snapshot()
    assert snapshot["robots"] == [
        {
            "robot_id": "AMR-1",
            "timestamp": None,
            "location_id": "UNKNOWN",
            "battery": 82.0,
            "status": "MOVING_TO_PICK",
            "load_quantity": 0,
            "x": 2.4,
            "y": 1.0,
            "next_cell_x": None,
            "next_cell_y": None,
        }
    ]


def test_visualizer_marks_amr_offline_from_last_will() -> None:
    state = WarehouseState()
    state.robots["AMR-1"] = {
        "robot_id": "AMR-1",
        "status": "MOVING_TO_PICK",
        "x": 2.4,
        "y": 1.0,
    }
    message = cast(
        mqtt.MQTTMessage,
        SimpleNamespace(
            topic=f"{GROUP_ID}/internal/amr/AMR-1/availability",
            payload=b'{"robot_id":"AMR-1","status":"offline"}',
        ),
    )

    state.on_message(cast(mqtt.Client, None), None, message)

    assert state.snapshot()["robots"][0]["status"] == "OFFLINE"
