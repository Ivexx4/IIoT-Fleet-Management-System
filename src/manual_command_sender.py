import argparse

from mqtt_config import GROUP_ID, MQTT_BROKER, MQTT_PORT, MQTT_QOS, create_mqtt_client
from protocol import encode_command


def send_command(robot_id: str, command: str, shelf_id: str, station_id: str) -> None:
    topic = f"warehouse/{GROUP_ID}/amr/{robot_id}/command"
    client = create_mqtt_client(
        "manual-command-sender",
        credential_role="command",
    )
    client.connect(MQTT_BROKER, MQTT_PORT, 60)
    client.loop_start()
    try:
        info = client.publish(
            topic,
            encode_command(command, shelf_id, station_id),
            qos=MQTT_QOS,
        )
        if info.rc != 0:
            raise RuntimeError(f"MQTT rejected the command for {robot_id}")
        info.wait_for_publish(timeout=5)
        if not info.is_published():
            raise TimeoutError(f"Broker did not acknowledge the command for {robot_id}")
    finally:
        client.loop_stop()
        client.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description="Send a test command to an AMR")
    parser.add_argument("robot_id")
    parser.add_argument("--charge", action="store_true")
    parser.add_argument("--shelf", default="S1")
    parser.add_argument("--station", default="P1")
    args = parser.parse_args()
    command = "FORCE_CHARGE" if args.charge else "EXECUTE_TASK"
    send_command(args.robot_id, command, args.shelf, args.station)


if __name__ == "__main__":
    main()
