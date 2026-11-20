#!/bin/sh
set -eu
cp /mosquitto/config/acl /mosquitto/data/acl
if [ -f /mosquitto/data/passwords ]; then
    mosquitto_passwd -b /mosquitto/data/passwords backend "$MQTT_BACKEND_PASSWORD"
else
    mosquitto_passwd -b -c /mosquitto/data/passwords backend "$MQTT_BACKEND_PASSWORD"
fi
mosquitto_passwd -b /mosquitto/data/passwords sim-01 "$MQTT_SIM_PASSWORD"
mosquitto_passwd -b /mosquitto/data/passwords esp32-01 "$MQTT_DEVICE_PASSWORD"
chown mosquitto:mosquitto /mosquitto/data/passwords /mosquitto/data/acl
chmod 600 /mosquitto/data/passwords /mosquitto/data/acl
exec mosquitto -c /mosquitto/config/mosquitto.conf
