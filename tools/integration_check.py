"""Verifica persistência, duplicação, validação e isolamento da fixture MQTT."""
import os
import time
from uuid import uuid4

import httpx
import psycopg

from simulate import sample, publish_fixture

base = 'http://backend:8000'
headers = {'Authorization': 'Bearer ' + os.environ['API_TOKEN']}
item = sample()
item.experiment_id = 'sim-check-' + uuid4().hex[:12]
payload = item.model_dump(mode='json')
with httpx.Client(base_url=base, timeout=10) as api:
    assert api.get('/health').status_code == 200
    assert api.post('/telemetry', json=payload).status_code in (401, 403)
    first = api.post('/telemetry', json=payload, headers=headers)
    assert first.status_code == 200 and first.json()['status'] == 'stored', first.text
    duplicate = api.post('/telemetry', json=payload, headers=headers)
    assert duplicate.status_code == 200 and duplicate.json()['status'] == 'duplicate'
    altered = dict(payload, soil_index=45)
    assert api.post('/telemetry', json=altered, headers=headers).status_code == 409
    assert api.post('/telemetry', json=dict(payload, soil_index=101), headers=headers).status_code == 422
    physical = dict(payload, source='physical', experiment_id='experiment-real')
    assert api.post('/telemetry', json=physical, headers=headers).status_code == 422
    rows = api.get('/telemetry', params={'experiment_id': item.experiment_id,
                   'source': 'simulation'}, headers=headers).json()
    assert len(rows) == 1
    assert api.get('/telemetry', params={'experiment_id': item.experiment_id,
               'source': 'physical'}, headers=headers).json() == []

# Fixture própria para verificar a identidade exata recebida pelo transporte MQTT.
mqtt_item = sample()
publish_fixture(mqtt_item)
deadline = time.monotonic() + 15
while True:
    with psycopg.connect(os.environ['DATABASE_URL']) as conn:
        count = conn.execute('SELECT count(*) FROM telemetry WHERE device_id=%s AND message_id=%s',
                             (mqtt_item.device_id, mqtt_item.message_id)).fetchone()[0]
    if count == 1:
        break
    if time.monotonic() > deadline:
        raise AssertionError('Fixture MQTT não foi persistida')
    time.sleep(0.5)
print('PASS: HTTP autenticado, validação, duplicidade, conflito, separação e persistência MQTT.')
