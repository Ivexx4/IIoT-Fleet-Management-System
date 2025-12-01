import paho.mqtt.client as mqtt
import struct
import time

# Configuration (Match these with your robot's settings)
#BROKER = "srsa-pi-8.dei.uc.pt"
#BROKER ="https://test.mosquitto.org/"
BROKER ="localhost"
PORT = 1883
GROUP_ID = "2024209219"  # Replace with your Group ID
ROBOT_ID = "RBT1"  # Replace with your Robot ID

TOPIC = f"warehouse/{GROUP_ID}/amr/{ROBOT_ID}/command"


def send_task(shelf_id, station_id):
    # Command Structure: [Command Type (1=Task), Shelf ID, Station ID]
    # struct.pack('BBB', ...) converts integers to 3 binary bytes
    payload = struct.pack('BBB', 1, shelf_id, station_id)

    client = mqtt.Client()  # Add CallbackAPIVersion.VERSION2 if using Paho 2.x
    client.connect(BROKER, PORT, 60)

    client.publish(TOPIC, payload)
    print(f"Sent TASK to {TOPIC}: Shelf={shelf_id}, Station={station_id}")
    client.disconnect()


def send_charge():
    # Command Structure: [Command Type (3=Charge), 0, 0]
    payload = struct.pack('BBB', 3, 0, 0)

    client = mqtt.Client()
    client.connect(BROKER, PORT, 60)

    client.publish(TOPIC, payload)
    print(f"Sent FORCE CHARGE to {TOPIC}")
    client.disconnect()


if __name__ == "__main__":
    # Example: Go to Shelf 5, then deliver to Station 2
    #for i in range(200):
    #    send_task(5, 2)
    #    time.sleep(120)
     #   send_charge()

    # Example: Force Charge
    send_task(1, 1)
