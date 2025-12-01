import sys
import time
import json
import random
import struct
import argparse
import paho.mqtt.client as mqtt
from datetime import datetime, timezone

# --- CONFIGURATION ---
#BROKER_ADDRESS="localhost"
BROKER_ADDRESS="srsa-pi-8.dei.uc.pt"
BROKER_PORT=1883
class Shelf:
    def __init__(self, group_id, zone_id, asset_id,update_time):
        self.group_id = group_id
        self.zone_id = zone_id
        self.asset_id = asset_id
        self.update_time = update_time
        self.config = self._get_initial_config()
        self.max_stock = self.config["stock"]
        self.topic_sub=f"warehouse/{group_id}/amr/+/status"
        self.topic_pub=f"warehouse/{group_id}/locations/{zone_id}/{asset_id}/status"
        # --- MQTT SETUP (Version Safe) ---
        self.topic = f"warehouse/{self.group_id}/locations/{self.zone_id}/{self.asset_id}/status"
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

    def _get_initial_config(self):
        """
        Internal method to set configuration based on Zone and Asset ID
        (Table 1 logic).
        """
        config = {
            "asset id": self.asset_id,
            "type": "SHELF",
            "item id": "UNKNOWN",
            "stock": 0,
            "unit": "units"
        }

        if self.zone_id == "storage-a":
            config["item id"] = "item_A"
            config["unit"] = "units"
            config["stock"] = 100
        elif self.zone_id == "storage-b":
            config["item id"] = "item_C"
            config["unit"] = "kg"
            config["stock"] = 200
        elif self.zone_id == "packing_zone":
            config["type"] = "PACK_STATION"
            config["item id"] = "N/A"
            config["unit"] = "status"
            config["stock"] = 0

        return config

    def on_connect(self, client, userdata, flags, reason_code, properties):
        """Paho 2.1 Connection Callback"""
        if reason_code.is_failure:
            print(f"[{self.asset_id}] Failed to connect: {reason_code}")
        else:
            print(f"[{self.asset_id}] Connected to MQTT Broker! (RC: {reason_code})")
            self.client.subscribe(self.topic_sub)

    def on_message(self, client, userdata, msg):
        """
        Esta função é chamada sempre que QUALQUER robô publica um estado.
        A prateleira verifica se o robô está à sua frente e a recolher material.
        """
        # Se for uma Packing Station, ignora lógica de stock
        if self.config["type"] != "SHELF":
            return

        try:
            payload = json.loads(msg.payload.decode())
            robot_location = payload.get("location_id", "")
            robot_status = payload.get("status", "")

            # Constrói o ID esperado (ex: Se sou S1, procuro por "SHELF-S1")
            my_location_tag = f"SHELF-{self.asset_id}"

            # LÓGICA DE CONSUMO:
            # 1. O robô diz que está na minha localização?
            # 2. O robô diz que está no estado "PICKING"?
            if robot_location == my_location_tag and robot_status == "PICKING":

                # VERIFICAÇÃO DE TEMPO (DEBOUNCE):
                # O estado PICKING dura 1s. Não queremos descontar 10x se o robô mandar 10 msg.
                # Só descontamos se já passaram pelo menos 2 segundos desde a última vez.
                # Reduz Stock (Simulação: retira 10 unidades ou valor aleatório)
                amount_to_take = 5
                if self.config["stock"] >= amount_to_take:
                        self.config["stock"] -= amount_to_take
                        print(f"[{self.asset_id}] ROBÔ DETETADO! Stock reduzido para {self.config['stock']}")
                else:
                        self.config["stock"] = 0
                        print(f"[{self.asset_id}] ROBÔ DETETADO! Stock esgotado.")
                self.publish_status()  # Publica atualização imediata
        except Exception as e:
            print(f"Error decoding message: {e}")

    def connect(self):
        """Connects to the broker and starts the loop"""
        try:
            print(f"--- Starting Shelf {self.asset_id} ({self.zone_id}) ---")
            print(f"Topic: {self.topic}")
            self.client.connect(BROKER_ADDRESS, BROKER_PORT, 60)
            self.client.loop_start()
        except Exception as e:
            print(f"Connection Failed: {e}")
            sys.exit(1)

    def refill(self):
        if self.config["type"] == "SHELF":
            if self.current_stock <= 0:
                print(f"[{self.asset_id}] Stock empty! Refilling in 2 seconds...")
                # Publish 0 status before waiting
                self.config["stock"] = 0
                self.publish_status()
                time.sleep(2)  # Refill delay
                self.config["stock"] = self.max_stock
                print(f"[{self.asset_id}] Refilled to {self.max_stock}.")
        elif self.config["type"] == "PACK_STATION":
            # Logic for pack station (if needed)
            pass

    def run(self):
        """Main execution loop"""
        self.connect()
        while True:
            if self.config["stock"]<=0:
                self.refill()
            self.publish_status()
            time.sleep(self.update_time)

    def publish_status(self):
        """Publishes the current configuration state to MQTT"""
        if self.config["type"]=="SHELF":
            payload = json.dumps(self.config)
        else:
            payload = json.dumps(self.config)
        print(f"[{time.strftime('%H:%M:%S')}] Publishing: {payload}")
        self.client.publish(self.topic, payload)



if __name__ == "__main__":

    parser = argparse.ArgumentParser(description="Simulate an AMR Robot.")
    parser.add_argument("group_id", help="The ID of the warehouse group")
    parser.add_argument("zone_id", help="The unique ID of the zone where this Shelve is")
    parser.add_argument("asset_id", help="The unique ID of this asset")
    parser.add_argument("update_time", help="The time interval between status updates (in seconds)", type=float)
    args = parser.parse_args()
    group_id = args.group_id
    zone_id = args.zone_id
    asset_id = args.asset_id
    update_time = args.update
    shelf = Shelf(group_id, zone_id, asset_id, update_time)
    shelf.run()

