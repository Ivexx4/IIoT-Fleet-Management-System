import sys
import time
import json
import random
import struct
import argparse
import paho.mqtt.client as mqtt
from datetime import datetime, timezone

# --- CONFIGURATION ---
STALL_PROBABILITY = 5  # 5% probability
#STALL_PROBABILITY = 0
BATTERY_DECAY_RATE = 1  # 1% per second
#BROKER="localhost"
BROKER="srsa-pi-8.dei.uc.pt"

class AMRRobot:
    def __init__(self, group_id, robot_id, broker_address, broker_port=1883):
        self.group_id = group_id
        self.robot_id = robot_id
        self.broker_address = broker_address
        self.broker_port = broker_port

        # State Variables
        self.status = "IDLE"  #
        self.location_id = "DOCK"  #
        self.battery = 100.0  #

        # Task Targets
        self.target_shelf = None
        self.target_station = None

        # Timing
        self.state_start_time = time.time()

        # --- MQTT SETUP (Version Safe) ---
        # This block ensures compatibility with both Paho 1.x and 2.x
        client_name = f"{group_id}_{robot_id}"

        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_name)

        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

        # Topics
        self.topic_pub = f"warehouse/{group_id}/amr/{robot_id}/status"
        self.topic_sub = f"warehouse/{group_id}/amr/{robot_id}/command"


    def connect(self):
        try:
            print(f"[{self.robot_id}] Connecting to Broker at {self.broker_address}...")
            self.client.connect(self.broker_address, self.broker_port, 60)
            self.client.loop_start()
            self.publish_status()
        except Exception as e:
            print(f"Connection Failed: {e}")
            sys.exit(1)

    def on_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0:
            print(f"[{self.robot_id}] Connected! Subscribing to {self.topic_sub}")
            client.subscribe(self.topic_sub)
        else:
            print(f"[{self.robot_id}] Connection Failed with code {rc}")

    def on_message(self, client, userdata, msg):
        try:
            # Payload validation
            if len(msg.payload) != 3:
                print(f"Error: Invalid payload length {len(msg.payload)}")
                return

            # Unpack bytes: Command, Target Shelf, Target Station
            cmd_type, shelf_byte, station_byte = struct.unpack('BBB', msg.payload)

            print(f"[{self.robot_id}] Received Command: Type={cmd_type}, Shelf={shelf_byte}, Station={station_byte}")

            # 0x03: FORCE_CHARGE (Override)
            if cmd_type == 0x03:
                print(f"[{self.robot_id}] !!! OVERRIDE: FORCE CHARGE !!!")
                self.change_state("MOVING_TO_CHARGE")
                self.location_id = "TRANSIT"
                return

            # 0x01: EXECUTE_TASK (Normal Operation)
            if self.status == "STALLED":
                print(f"[{self.robot_id}] Ignored Task (STALLED)")
                return

            if cmd_type == 0x01 and self.status == "IDLE":
                self.target_shelf = f"SHELF-S{shelf_byte}"  #
                self.target_station = f"STATION-P{station_byte}"  #
                self.change_state("MOVING_TO_PICK")
                self.location_id = "TRANSIT"


        except Exception as e:
            print(f"Error decoding message: {e}")

    def change_state(self, new_state):
        self.status = new_state
        self.state_start_time = time.time()
        print(f"[{self.robot_id}] State changed to: {self.status}")
        self.publish_status()


    def check_failure(self):
        # 5% Stalling logic
        if "MOVING" in self.status:
            if random.randint(1, 100) <= STALL_PROBABILITY:
                print(f"[{self.robot_id}] !!! FAILURE: ROBOT STALLED !!!")
                self.change_state("STALLED")
                return True
        return False

    def update_logic(self):
        now = time.time()
        elapsed_in_state = now - self.state_start_time

        # --- BATTERY LOGIC ---
        active_states = ["MOVING_TO_PICK", "PICKING", "MOVING_TO_DROP", "DROPPING", "MOVING_TO_CHARGE"]
        if self.status in active_states:
            self.battery -= BATTERY_DECAY_RATE
            if self.battery < 0: self.battery = 0

        # --- STATE MACHINE ---
        if self.status == "IDLE":
            self.location_id = "DOCK"

        elif self.status == "MOVING_TO_PICK":
            if not self.check_failure():
                if elapsed_in_state >= 3.0:
                    self.location_id = self.target_shelf
                    self.change_state("PICKING")


        elif self.status == "PICKING":
            if elapsed_in_state >= 1.0:
                self.location_id = "TRANSIT"
                self.change_state("MOVING_TO_DROP")

        elif self.status == "MOVING_TO_DROP":
            if not self.check_failure():
                if elapsed_in_state >= 2.0:  #
                    self.location_id = self.target_station
                    self.change_state("DROPPING")

        elif self.status == "DROPPING":
            if elapsed_in_state >= 1.0:
                self.location_id = "DOCK"
                self.change_state("IDLE")

        elif self.status == "MOVING_TO_CHARGE":
            if elapsed_in_state >= 3.0:
                self.location_id = "CHARGING_STATION"
                self.change_state("CHARGING")


        elif self.status == "CHARGING":
            if elapsed_in_state >= 10.0:  #
                self.battery = 100.0
                self.location_id = "DOCK"
                self.change_state("IDLE")


        elif self.status == "STALLED":
            pass

    def publish_status(self):
        # JSON Payload with Timezone aware timestamp
        payload = {
            "robot_id": self.robot_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "location_id": self.location_id,
            "battery": int(self.battery),
            "status": self.status
        }
        json_payload = json.dumps(payload)
        print(f"[{self.robot_id}] Publishing: {json_payload}")
        self.client.publish(self.topic_pub, json_payload)

    def run(self):
        self.connect()
        try:
            while True:
                self.update_logic()

                time.sleep(0.1)  #
        except KeyboardInterrupt:
            print("Stopping Robot...")
            self.client.loop_stop()
            self.client.disconnect()


if __name__ == "__main__":
    # --- ARGUMENT PARSING ---
    parser = argparse.ArgumentParser(description="Simulate an AMR Robot.")

    # Positional arguments
    parser.add_argument("group_id", help="The ID of the warehouse group")
    parser.add_argument("robot_id", help="The unique ID of this robot")

    args = parser.parse_args()


    bot = AMRRobot(args.group_id, args.robot_id, BROKER)
    bot.run()