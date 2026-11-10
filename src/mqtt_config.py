import os
import ssl

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

load_dotenv()

GROUP_ID = os.getenv("WAREHOUSE_GROUP_ID", "2024209219")
MQTT_BROKER = os.getenv("MQTT_BROKER", "127.0.0.1")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USERNAME = os.getenv("MQTT_USERNAME")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD")
MQTT_TLS_CA_CERT = os.getenv("MQTT_TLS_CA_CERT")
MQTT_QOS = 1

INFLUX_URL = os.getenv("INFLUX_URL", "http://127.0.0.1:8086")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN")
INFLUX_ORG = os.getenv("INFLUX_ORG", "warehouse")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "warehouse_data")
REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")


def create_mqtt_client(client_id: str = "") -> mqtt.Client:
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=client_id,
    )
    if MQTT_USERNAME:
        client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    if MQTT_TLS_CA_CERT:
        client.tls_set(
            ca_certs=MQTT_TLS_CA_CERT,
            cert_reqs=ssl.CERT_REQUIRED,
            tls_version=ssl.PROTOCOL_TLS_CLIENT,
        )
    return client