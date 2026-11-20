"""Ciclo real MQTT/TLS/API/PostgreSQL com dispositivo e medições SIMULADOS."""
import json
import os
import threading
import time
from datetime import datetime, timezone
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import paho.mqtt.client as mqtt
import psycopg

from simulate import run_loop


def wait_for(predicate, timeout=20):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.25)
    raise AssertionError('Timeout aguardando condição de integração')


def check_acl():
    """MQTT5 PUBACK permite verificar negação do tópico pelo broker, não apenas worker."""
    ready, published = threading.Event(), threading.Event()
    reasons = []
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id='sim-acl-check', protocol=mqtt.MQTTv5)
    client.username_pw_set('sim-01', os.environ['MQTT_SIM_PASSWORD'])
    client.tls_set(ca_certs='/certs/ca.crt')
    client.on_connect = lambda *args: ready.set()
    def on_publish(client, userdata, mid, reason, properties):
        reasons.append(reason)
        published.set()
    client.on_publish = on_publish
    client.connect(os.environ.get('MQTT_HOST', 'mosquitto'), 8883)
    client.loop_start()
    try:
        assert ready.wait(10), 'Sem conexão TLS'
        client.publish('irrigation/v1/sim-other/telemetry', '{}', qos=1)
        assert published.wait(10), 'Sem PUBACK de ACL'
        assert reasons[-1].is_failure, f'Tópico alheio autorizado: {reasons[-1]}'
        client.publish('irrigation/v1/sim-01/telemetry','{invalid',qos=1).wait_for_publish(timeout=10)
    finally:
        client.disconnect()
        client.loop_stop()


def main():
    headers = {'Authorization': 'Bearer '+os.environ['API_TOKEN']}
    config = dict(device_id='sim-01', experiment_id='sim-integration', unit_id='sim-pot-01',
                  treatment='T1', source='simulation', enabled=True, calibration_id='sim-fixture-v1',
                  soil_low=30, soil_high=60, min_reservoir_mass_g=100, target_volume_ml=25,
                  max_duration_s=10, max_telemetry_age_s=10, cooldown_s=1, schedule_interval_s=1)
    check_acl()
    stop = threading.Event()
    started_at = datetime.now(timezone.utc)
    thread = threading.Thread(target=run_loop, kwargs={'seconds': 40, 'stop_event': stop}, daemon=True)
    thread.start()
    with httpx.Client(base_url='http://backend:8000', headers=headers, timeout=10) as api:
        wait_for(lambda:len(api.get('/rejections',params={'device_id':'sim-01'}).json())>0)
        def rows():
            return api.get('/telemetry', params={'experiment_id':'sim-integration', 'source':'simulation'}).json()
        wait_for(lambda: any(row['payload']['soil_index'] == 25 and row['payload']['treatment']=='T1' and
                 datetime.fromisoformat(row['payload']['collected_at'])>=started_at for row in rows()))
        try:
            response = api.post('/control/config', json=config)
            assert response.status_code == 200, response.text
            request = {'device_id':'sim-01', 'unit_id':'sim-pot-01', 'experiment_id':'sim-integration', 'request_id':str(uuid4())}
            result = api.post('/control/evaluate', json=request)
            assert result.status_code == 200, result.text
            decision = result.json()
            assert decision['actuate'], decision
            duplicate = api.post('/control/evaluate', json=request)
            assert duplicate.json() == decision, duplicate.text
            command_id = decision['command_id']
            def completed():
                commands = api.get('/commands', params={'device_id':'sim-01'}).json()
                return any(row['payload']['command_id'] == command_id and row['status'] == 'completed' for row in commands)
            wait_for(completed)
            with psycopg.connect(os.environ['DATABASE_URL']) as conn:
                events = conn.execute('SELECT payload FROM command_events WHERE command_id=%s ORDER BY occurred_at', (command_id,)).fetchall()
            payloads = [row[0] for row in events]
            assert [event['status'] for event in payloads] == ['accepted','started','completed'], payloads
            assert payloads[-1]['volume_ml'] == 25 and payloads[-1]['energy_wh'] > 0
            response = api.post('/events', json=payloads[-1])
            assert response.json()['status'] == 'duplicate', response.text
            stop.set()
            thread.join(timeout=5)
            assert not thread.is_alive(), 'Loop simulado não encerrou'
            time.sleep(1.1)  # Separar conclusão do cooldown configurado, sem falsificar horário.
            from simulate import sample
            # Manual commands também devem respeitar config, TTL e bloqueio físico.
            now = datetime.now(timezone.utc)
            manual = dict(schema_version=1, command_id=str(uuid4()), device_id='sim-01',
                          experiment_id='sim-integration', unit_id='sim-pot-01', treatment='T1',
                          source='simulation', issued_at=now.isoformat(),
                          expires_at=(now+timedelta(seconds=30)).isoformat(), target_volume_ml=25,
                          max_duration_s=10, reason='sim-integration-negative-check')
            for changes in [dict(target_volume_ml=26), dict(max_duration_s=11),
                            dict(issued_at=(now-timedelta(seconds=40)).isoformat(), expires_at=(now-timedelta(seconds=10)).isoformat()),
                            dict(expires_at=(now+timedelta(minutes=6)).isoformat()),
                            dict(source='physical', device_id='device-01', experiment_id='experiment-real')]:
                response = api.post('/commands', json=dict(manual, command_id=str(uuid4()), **changes))
                assert response.status_code == 422, response.text
            bad_reading = sample()
            bad_reading.sensor_valid = False
            bad_reading.soil_index = None
            api.post('/telemetry', json=bad_reading.model_dump(mode='json')).raise_for_status()
            response = api.post('/commands', json=dict(manual, command_id=str(uuid4())))
            assert response.status_code == 422 and 'invalid_or_uncalibrated_sensor' in response.text, response.text
            # T3 sem modelo: precisa bloquear, jamais inventar previsão.
            config.update(treatment='T3', prediction_fallback_enabled=False)
            api.post('/control/config', json=config).raise_for_status()
            item = sample()
            item.treatment = 'T3'
            item.soil_index = 25
            api.post('/telemetry', json=item.model_dump(mode='json')).raise_for_status()
            gap = api.post('/control/evaluate', json={'device_id':'sim-01','unit_id':'sim-pot-01','experiment_id':'sim-integration','request_id':str(uuid4())}).json()
            assert not gap['actuate'], gap
            assert gap['reason'] == 'prediction_unavailable', gap
        finally:
            stop.set()
            thread.join(timeout=5)
            config['enabled'] = False
            api.post('/control/config', json=config).raise_for_status()
    print('PASS: TLS/ACL, decisão idempotente, MQTT comando→aceitação→início→conclusão, métricas sintéticas e lacuna de modelo bloqueada.')


if __name__ == '__main__':
    main()
