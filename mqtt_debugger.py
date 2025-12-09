
import sys
import paho.mqtt.client as mqtt_subscribe  # import library
from datetime import datetime
from mqqtt_config import MQTT_BROKER,MQTT_PORT,GROUP_ID

TOPIC_WAREHOUSE = f"warehouse/{GROUP_ID}/#"
TOPIC_INTERNAL = f"{GROUP_ID}/internal/#"


def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code == 0:
        print(f"✅ Ligado ao Broker MQTT ({MQTT_BROKER})!")
        print(f"📡 A escutar:\n   - {TOPIC_WAREHOUSE}\n   - {TOPIC_INTERNAL}")
        print("-" * 50)
    else:
        print(f"❌ Falha na ligação, código de retorno: {reason_code}")
    client.subscribe(TOPIC_WAREHOUSE)
    client.subscribe(TOPIC_INTERNAL)

def on_message(client, userdata, msg):
    # Requisito do enunciado: [time]: [topic]: [message]
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        payload = msg.payload.decode('utf-8')
    except UnicodeDecodeError:
        # Caso sejam dados binários (como os comandos do robô), mostra a representação em bytes
        payload = str(msg.payload)

    print(f"[{current_time}]: [{msg.topic}]: {payload}")



if __name__ == "__main__":
    if len(sys.argv) > 1:
        GROUP_ID = sys.argv[1]
        TOPIC_WAREHOUSE = f"warehouse/{GROUP_ID}/#"
        TOPIC_INTERNAL = f"{GROUP_ID}/internal/#"

    client = mqtt_subscribe.Client(callback_api_version=mqtt_subscribe.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message
    try:
        client.connect(MQTT_BROKER, MQTT_PORT)

    except Exception as e:
        print(f"Erro ao conectar: {e}")
        sys.exit(1)

    try:
        client.loop_forever()
    except KeyboardInterrupt:
        print("\n🛑 Exiting debugger")