# tested on Python 3.9.6, with Mosquitto 2.0.11
# uses paho-mqtt-2.1.0 (the syntax is different from paho-mqtt-1.x.x)

import sys
import paho.mqtt.client as mqtt_subscribe  # import library
from datetime import datetime

# --- CONFIGURAÇÃO ---
GROUP_ID = "2024209219"
#MQTT_BROKER ="localhost"
MQTT_BROKER = "srsa-pi-8.dei.uc.pt"
MQTT_PORT = 1883


# Tópicos a subscrever (Baseado no Enunciado Seção 6.6) [cite: 2104-2107]
TOPIC_WAREHOUSE = f"warehouse/{GROUP_ID}/#"
TOPIC_INTERNAL = f"{GROUP_ID}/internal/#"


# The callback for when the client receives a CONNACK response from the server.
def on_connect(client, userdata, flags, reason_code, properties):
    # For paho-mqtt 2.0.0+, you need to have the properties parameter
    if reason_code == 0:
        print(f"✅ Ligado ao Broker MQTT ({MQTT_BROKER})!")
        print(f"📡 A escutar:\n   - {TOPIC_WAREHOUSE}\n   - {TOPIC_INTERNAL}")
        print("-" * 50)
    else:
        print(f"❌ Falha na ligação, código de retorno: {reason_code}")

    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.
    client.subscribe(TOPIC_WAREHOUSE)
    client.subscribe(TOPIC_INTERNAL)


# The callback for when a PUBLISH message is received from the server.
def on_message(client, userdata, msg):
    # Requisito do enunciado: [time]: [topic]: [message]
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        payload = msg.payload.decode('utf-8')
    except UnicodeDecodeError:
        # Caso sejam dados binários (como os comandos do robô), mostra a representação em bytes
        payload = str(msg.payload)

    print(f"[{current_time}]: [{msg.topic}]: {payload}")


# --- MAIN ---
if __name__ == "__main__":
    # Permite passar o GroupID pela linha de comandos: python mqtt_debugger.py 12345
    if len(sys.argv) > 1:
        GROUP_ID = sys.argv[1]
        TOPIC_WAREHOUSE = f"warehouse/{GROUP_ID}/#"
        TOPIC_INTERNAL = f"{GROUP_ID}/internal/#"

    # For paho-mqtt 2.0.0+, you need to set callback_api_version.
    client = mqtt_subscribe.Client(callback_api_version=mqtt_subscribe.CallbackAPIVersion.VERSION2)

    # Configurar Autenticação (se necessário)

    # set callback function on connecting
    client.on_connect = on_connect

    # set callback function on new message received
    client.on_message = on_message

    # Connect to MQTT broker
    try:
        client.connect(MQTT_BROKER, MQTT_PORT)
    except Exception as e:
        print(f"Erro ao conectar: {e}")
        sys.exit(1)

    try:
        # use client.loop_forever() to block the code forever to check for data
        client.loop_forever()
    except KeyboardInterrupt:
        print("\n🛑 Exiting debugger")