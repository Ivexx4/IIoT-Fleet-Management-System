import json
import struct
import socket
import threading
import time
import re
from datetime import datetime

# Third-party libraries
import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion
from influxdb_client_3 import InfluxDBClient3,Point


# ==========================================
# CONFIGURATION
# ==========================================
GROUP_ID = "2024209219"  # Replace with your actual Group ID (Student Number) [cite: 110]
MQTT_BROKER = "localhost"  # Or "srsa-pi-8.dei.uc.pt" if onsite [cite: 162]

MQTT_PORT = 1883
UDP_IP = "0.0.0.0"
# UDP Configuration (Alert Server)
UDP_PORT = 9090  # [cite: 177]

# InfluxDB Configuration
INFLUX_URL = "https://eu-central-1-1.aws.cloud2.influxdata.com"  # Example Cloud URL [cite: 233]
INFLUX_TOKEN = "dG2CP2aLbN7EO1WC1LMZkkEGu5QnKLkrvHGCygn0rdelNa0w_LPEcnBJCkaGM2XmSwnqv-4ATgrk7I6hZX6eAg=="
INFLUX_ORG = "ivo rosa"
INFLUX_BUCKET = "warehouse_data"

# Topics
# Subscribe to all raw sensor topics (Robots + Shelves)
TOPIC_SENSORS_WILDCARD = f"warehouse/{GROUP_ID}/#"  # [cite: 167]
# Subscribe to Task Dispatch from Coordinator
TOPIC_TASK_DISPATCH = f"{GROUP_ID}/internal/tasks/dispatch"  # [cite: 176]


# ==========================================
# GATEWAY LOGIC
# ==========================================

class WarehouseGateway:
    def __init__(self):
        # Initialize MQTT Client with Paho 2.1 API Version
        self.mqtt_client = mqtt.Client(CallbackAPIVersion.VERSION2)
        self.mqtt_client.on_connect = self.on_connect
        self.mqtt_client.on_message = self.on_message

        # UPDATED: Initialize InfluxDB Client 3
        # Client 3 simplifies the connection; no separate WriteAPI object needed
        self.influx_client = InfluxDBClient3(
            host=INFLUX_URL,
            token=INFLUX_TOKEN,
            org=INFLUX_ORG,
            database=INFLUX_BUCKET
        )

        # Initialize UDP Socket for High Priority Alerts [cite: 177]
        self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp_sock.bind((UDP_IP, UDP_PORT))

        print(f"[{datetime.now()}] Gateway Initialized for {GROUP_ID}")

    def start(self):
        try:
            self.mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
            self.mqtt_client.loop_start()
        except Exception as e:
            print(f"Error connecting to MQTT Broker: {e}")

        # Start UDP Server in background thread
        udp_thread = threading.Thread(target=self.run_udp_server)
        udp_thread.daemon = True
        udp_thread.start()

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("Stopping Gateway...")
            self.mqtt_client.loop_stop()
            self.udp_sock.close()
            self.influx_client.close()

    # ------------------------------------------
    # MQTT HANDLING (Paho 2.x)
    # ------------------------------------------
    def on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            print("Connected to MQTT Broker.")
            client.subscribe(TOPIC_SENSORS_WILDCARD)
            client.subscribe(TOPIC_TASK_DISPATCH)
            print(f"Subscribed to: {TOPIC_SENSORS_WILDCARD}, {TOPIC_TASK_DISPATCH}")
        else:
            print(f"Failed to connect, reason code: {reason_code}")

    def on_message(self, client, userdata, msg):
        topic = msg.topic
        payload_str = msg.payload.decode('utf-8')

        try:
            if topic == TOPIC_TASK_DISPATCH:
                # Handle JSON commands from Coordinator
                self.process_command_json(payload_str)

            elif topic.startswith(f"warehouse/{GROUP_ID}/"):
                # Handle Raw Sensor Data [cite: 178]
                self.process_sensor_data(topic, payload_str)

        except Exception as e:
            print(f"Error processing message on {topic}: {e}")

    # ------------------------------------------
    # UDP SERVER (System Monitor Alerts)
    # ------------------------------------------
    def run_udp_server(self):
        """
        Receives high-priority alert messages from the System Monitor on port 9090[cite: 177].
        """
        print(f"UDP Server listening on port {UDP_PORT}...")
        while True:
            try:
                data, addr = self.udp_sock.recvfrom(1024)
                message = data.decode('utf-8')
                print(f"Received UDP Alert from {addr}: {message}")

                # Check if it's an override command (FORCE_CHARGE)
                if "FORCE_CHARGE" in message or "EXECUTE_TASK" in message:
                    self.process_command_json(message)
                else:
                    # Log generic alerts to InfluxDB
                    p = Point("system_alerts").tag("source", "UDP").field("message", message)
                    self.influx_client.write(record=p)

            except Exception as e:
                print(f"UDP Error: {e}")

    # ------------------------------------------
    # SENSOR DATA PROCESSING & STANDARDIZATION
    # ------------------------------------------
    def process_sensor_data(self, topic, payload_str):
        """
        Extracts, standardizes (Table 1), stores in InfluxDB, and forwards to internal topics.
        """
        try:
            data = json.loads(payload_str)

            # 1. STANDARDIZATION (Table 1)
            # storage-a: Already in units (1 unit = 23kg).
            # storage-b: In kg. Needs conversion: Units = kg / 23.

            if "storage-b" in topic or ("zone_id" in data and data["zone_id"] == "storage-b"):
                if "stock" in data:
                    raw_kg = float(data["stock"])
                    # Convert kg to units
                    data["stock_units"] = int(raw_kg / 23)
                    data["unit"] = "units (converted)"
            elif "stock" in data:
                # Assume storage-a or others are already units
                data["stock_units"] = int(data["stock"])

            # 2. STORE IN INFLUXDB (Client 3 Usage) [cite: 180]
            # In Client 3, we simply call .write() on the client instance
            if "robot_id" in data:

                point = Point("Robots_status").tag("robot_id", data["robot_id"]).tag("status", data["status"]).field("location", data["location_id"]).field("battery", float(data["battery"]))
                self.influx_client.write(record=point)
            if "asset_id" in data:
                if data["type"]=="SHELF":
                    point = Point("Shelves").tag("asset_id", data["asset_id"]).field("stock", data["stock_units"])
                    self.influx_client.write(record=point)
            internal_topic = ""
            if "/amr/" in topic:
                robot_id = data.get("robot_id", "unknown")
                internal_topic = f"{GROUP_ID}/internal/amr/{robot_id}/status"
            elif "/locations/" in topic:
                asset_id = data.get("asset_id", "unknown")
                internal_topic = f"{GROUP_ID}/internal/static/{asset_id}/status"

            if internal_topic:
                self.mqtt_client.publish(internal_topic, json.dumps(data))

        except json.JSONDecodeError:
            print("Failed to decode sensor JSON")
        except Exception as e:
            print(f"InfluxDB Write Error: {e}")

    # ------------------------------------------
    # COMMAND ENCODING (Table 2)
    # ------------------------------------------
    def process_command_json(self, json_str):
        """
        Translates JSON commands into 3-byte binary format and publishes to specific robot[cite: 185, 189].
        """
        try:
            cmd_data = json.loads(json_str)
            robot_id = cmd_data.get("robot_id")

            if not robot_id:
                print("Error: Command missing robot_id")
                return

            # --- BYTE 1: Command Type ---
            cmd_type_str = cmd_data.get("command", "")
            cmd_byte = 0x00

            if cmd_type_str == "EXECUTE_TASK":
                cmd_byte = 0x01
            elif cmd_type_str == "FORCE_CHARGE":
                cmd_byte = 0x03
            else:
                print(f"Unknown command type: {cmd_type_str}")
                return

            # --- BYTE 2: Target Shelf ID ---
            shelf_raw = cmd_data.get("target_shelf_id", "S0")
            shelf_byte = 0
            if isinstance(shelf_raw, str):
                nums = re.findall(r'\d+', shelf_raw)
                if nums: shelf_byte = int(nums[0])
            elif isinstance(shelf_raw, int):
                shelf_byte = shelf_raw

            # --- BYTE 3: Target Station ID ---
            station_raw = cmd_data.get("target_station_id", "P0")
            station_byte = 0
            if isinstance(station_raw, str):
                nums = re.findall(r'\d+', station_raw)
                if nums: station_byte = int(nums[0])
            elif isinstance(station_raw, int):
                station_byte = station_raw

            # --- ENCODING & SENDING ---
            # Struct format 'BBB' = 3 unsigned chars (1 byte each) -> Total 3 Bytes [cite: 184]
            binary_payload = struct.pack('BBB', cmd_byte, shelf_byte, station_byte)

            # Topic: warehouse/{GroupID}/amr/{robot_id}/command [cite: 104]
            target_topic = f"warehouse/{GROUP_ID}/amr/{robot_id}/command"

            print(f"Sending Binary Command to {target_topic}: {binary_payload.hex().upper()}")
            self.mqtt_client.publish(target_topic, binary_payload)

        except Exception as e:
            print(f"Error encoding command: {e}")


if __name__ == "__main__":
    gateway = WarehouseGateway()
    gateway.start()