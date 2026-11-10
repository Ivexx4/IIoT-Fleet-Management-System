import os


GROUP_ID = os.getenv("WAREHOUSE_GROUP_ID", "2024209219")
MQTT_BROKER = os.getenv("MQTT_BROKER", "127.0.0.1")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))

INFLUX_URL = os.getenv(
    "INFLUX_URL",
    "https://eu-central-1-1.aws.cloud2.influxdata.com",
)
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN")
INFLUX_ORG = os.getenv("INFLUX_ORG", "ivo rosa")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "warehouse_data")
