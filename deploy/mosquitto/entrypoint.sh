#!/bin/sh
set -eu

: "${MQTT_USERNAME:?MQTT_USERNAME is required}"
: "${MQTT_PASSWORD:?MQTT_PASSWORD is required}"
: "${HOST_IP:=127.0.0.1}"
mkdir -p /mosquitto/data /mosquitto/certs

if [ ! -s /mosquitto/data/ca.crt ] || [ ! -s /mosquitto/data/ca.key ]; then
    openssl req -x509 -newkey rsa:3072 -nodes -days 3650 \
        -keyout /mosquitto/data/ca.key \
        -out /mosquitto/data/ca.crt \
        -subj "/CN=Warehouse MQTT Local CA" \
        -addext "basicConstraints=critical,CA:TRUE" \
        -addext "keyUsage=critical,keyCertSign,cRLSign"
fi

HOST_CERT_VALID=false
if [ -s /mosquitto/data/server.crt ]; then
    openssl x509 -in /mosquitto/data/server.crt -checkip "$HOST_IP" -noout >/dev/null 2>&1 && HOST_CERT_VALID=true
fi

if [ "$HOST_CERT_VALID" != true ]; then
    openssl req -new -newkey rsa:2048 -nodes \
        -keyout /mosquitto/data/server.key \
        -out /mosquitto/data/server.csr \
        -subj "/CN=mosquitto"
    printf "subjectAltName=DNS:mosquitto,DNS:localhost,IP:127.0.0.1,IP:%s\nextendedKeyUsage=serverAuth\n" "$HOST_IP" \
        > /mosquitto/data/server.ext
    openssl x509 -req -days 825 \
        -in /mosquitto/data/server.csr \
        -CA /mosquitto/data/ca.crt \
        -CAkey /mosquitto/data/ca.key \
        -CAcreateserial \
        -out /mosquitto/data/server.crt \
        -extfile /mosquitto/data/server.ext
fi

cp /mosquitto/data/ca.crt /mosquitto/certs/ca.crt
mosquitto_passwd -b -c /mosquitto/data/passwordfile "$MQTT_USERNAME" "$MQTT_PASSWORD"
chown -R mosquitto:mosquitto /mosquitto/data /mosquitto/certs
exec su-exec mosquitto mosquitto -c /mosquitto/config/mosquitto.conf
