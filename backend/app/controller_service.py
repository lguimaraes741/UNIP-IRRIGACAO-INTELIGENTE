"""Avaliação automática: cada origem (simulação/física) só com seu interruptor explícito."""
import json
import logging
import os
import time
from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid5

from fastapi import HTTPException
from app.contracts import DecisionConfiguration
from app.control import configuration_key, source_enabled
from app.ml_api import PredictionRequest, predict
from app.store import connect, evaluate

log = logging.getLogger('control')
scan_offset=0
HEARTBEAT_S = 15  # Firmware treats >90 s without heartbeat as decision service offline.


def tick_once():
    with connect() as conn:
        configurations = conn.execute("SELECT payload FROM control_configs ORDER BY device_id,payload->>'experiment_id',payload->>'unit_id'").fetchall()
    global scan_offset
    if configurations:
        offset=scan_offset % len(configurations)
        configurations=configurations[offset:]+configurations[:offset]
        scan_offset += 1
    count = 0
    for row in configurations:
        config = DecisionConfiguration.model_validate(row['payload'])
        if not config.enabled or not source_enabled(config.source):
            continue
        with connect() as conn:
            reading = conn.execute('SELECT message_id FROM telemetry WHERE device_id=%s AND experiment_id=%s AND source=%s AND unit_id=%s ORDER BY collected_at DESC LIMIT 1',
                                  (config.device_id,config.experiment_id,config.source,config.unit_id)).fetchone()
            if not reading:
                continue
            request_id = uuid5(NAMESPACE_URL, configuration_key(config) + str(reading['message_id']))
            if conn.execute('SELECT 1 FROM decisions WHERE request_id=%s', (request_id,)).fetchone():
                continue
        if config.treatment == 'T3':
            try:
                predict(PredictionRequest(device_id=config.device_id,telemetry_message_id=reading['message_id']))
            except HTTPException as exc:
                log.warning('Previsão indisponível: %s', exc.detail)
        evaluate(config.device_id, request_id,config.unit_id,config.experiment_id)
        count += 1
    return count


def heartbeat_devices():
    with connect() as conn:
        rows = conn.execute("SELECT DISTINCT device_id, payload->>'source' AS source FROM control_configs WHERE (payload->>'enabled')::boolean").fetchall()
    # Without the switch for its source, no heartbeat: the board keeps its local fallback.
    return sorted({row['device_id'] for row in rows if source_enabled(row['source'])})


def publish_heartbeats(client, devices):
    sent_at = datetime.now(timezone.utc).isoformat()
    for device in devices:
        client.publish(f'irrigation/v1/{device}/heartbeat', json.dumps({'schema_version': 1, 'sent_at': sent_at}), qos=0, retain=False)


if __name__ == '__main__':
    from app.worker import make_client
    logging.basicConfig(level=logging.INFO)
    client = make_client(client_id='tcc-controller-v1')
    client.on_message = None
    client.on_connect = None
    client.connect(os.environ.get('MQTT_HOST', 'mosquitto'), 8883, keepalive=30)
    client.loop_start()
    last_heartbeat = 0.0
    while True:
        try:
            tick_once()
            # Only a loop that just reached the database advertises liveness.
            if time.monotonic() - last_heartbeat >= HEARTBEAT_S:
                publish_heartbeats(client, heartbeat_devices())
                last_heartbeat = time.monotonic()
        except Exception:
            log.exception('Avaliação falhou; nenhum comando é presumido como executado')
        time.sleep(2)
