"""Fixture de transporte MQTT. Não simula dinâmica agronômica nem liga bombas."""
import os
import argparse
import json
import time
from datetime import datetime, timezone
from uuid import uuid4

import paho.mqtt.client as mqtt

from app.contracts import Telemetry
from device_simulator import DeviceSimulator, MultiVesselSimulator


def sample():
    return Telemetry(schema_version=1, message_id=uuid4(), device_id='sim-01',
        experiment_id='sim-integration', unit_id='sim-pot-01', treatment='T1',
        source='simulation', collected_at=datetime.now(timezone.utc),
        calibration_id='sim-fixture-v1', soil_raw=2000, soil_index=50,
        sensor_valid=True, temperature_c=24, humidity_pct=60, reservoir_mass_g=1500,
        pump_voltage_v=0, pump_current_a=0)


def publish_fixture(item):
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id='tcc-simulator')
    client.username_pw_set('sim-01', os.environ['MQTT_SIM_PASSWORD'])
    client.tls_set(ca_certs='/certs/ca.crt')
    client.connect(os.environ.get('MQTT_HOST', 'mosquitto'), 8883)
    client.loop_start()
    # Mesmo identificador e conteúdo: testar idempotência de forma explícita.
    for _ in range(2):
        result = client.publish('irrigation/v1/sim-01/telemetry', item.model_dump_json(), qos=1)
        result.wait_for_publish(timeout=10)
        if not result.is_published():
            raise RuntimeError('Publicação não confirmada pelo broker')
    client.disconnect()
    client.loop_stop()
    return item


def run_loop(state_path=None, seconds=60, stop_event=None, treatment='T1', fallback_enabled=False,
             offline_after_s=None, offline_for_s=0, three_units=False):
    """Transporte real, medições explicitamente sintéticas; fixture 5 mL/s e 3 W."""
    from app.contracts import IrrigationCommand
    import threading
    device = MultiVesselSimulator(state_path) if three_units else DeviceSimulator(state_path)
    if treatment not in ('T1', 'T2', 'T3'):
        raise ValueError('Tratamento desconhecido')
    device.treatment = treatment
    device.fallback_enabled = fallback_enabled
    lock = threading.RLock()
    topic = 'irrigation/v1/sim-01/'
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id='tcc-simulator-loop')
    client.username_pw_set('sim-01', os.environ['MQTT_SIM_PASSWORD'])
    client.tls_set(ca_certs='/certs/ca.crt')
    connected = threading.Event()

    def enqueue(events):
        for event in events:
            if any(item['topic'] == topic+'events' and item['payload'].get('event_id') == event['event_id'] for item in device.buffer):
                continue
            device.queue(topic+'events', event)

    def on_connect(client, userdata, flags, reason, properties):
        if reason == 0:
            client.subscribe(topic+'commands', qos=1)
    def on_subscribe(client, userdata, mid, reasons, properties):
        if all(not code.is_failure for code in reasons):
            with lock:
                device.resync(datetime.now(timezone.utc))
            connected.set()
    def on_disconnect(client, userdata, flags, reason, properties):
        connected.clear()
        with lock:
            device.disconnect()
    def on_message(client, userdata, message):
        try:
            command = IrrigationCommand.model_validate_json(message.payload)
            with lock:
                enqueue(device.receive(command.model_dump(mode='json'), datetime.now(timezone.utc)))
        except (ValueError, TypeError):
            print('Comando inválido rejeitado sem atuação simulada', flush=True)
    client.on_connect, client.on_subscribe = on_connect, on_subscribe
    client.on_disconnect, client.on_message = on_disconnect, on_message
    client.connect(os.environ.get('MQTT_HOST', 'mosquitto'), 8883)
    client.loop_start()
    started_at = time.monotonic()
    deadline, next_read = started_at+seconds, 0
    disconnected_at = None
    offline_done = False
    try:
        while time.monotonic() < deadline and not (stop_event and stop_event.is_set()):
            if offline_after_s is not None and not offline_done and disconnected_at is None and time.monotonic()-started_at >= offline_after_s:
                client.disconnect()
                client.loop_stop()
                disconnected_at = time.monotonic()
            if disconnected_at is not None and time.monotonic()-disconnected_at >= offline_for_s:
                client.reconnect()
                client.loop_start()
                disconnected_at = None
                offline_done = True
            now = datetime.now(timezone.utc)
            with lock:
                enqueue(device.tick(now))
                if connected.is_set() and not device.remote_ready and not device.active:
                    device.resync(now)
                if not connected.is_set():
                    enqueue(device.fallback(now))
                if time.monotonic() >= next_read:
                    readings = device.units.items() if three_units else [(device.unit_id,dict(
                        treatment=treatment, soil_index=device.soil_index, sensor_valid=device.sensor_valid))]
                    for unit, state in readings:
                        item = sample()
                        item.unit_id, item.experiment_id = unit, device.experiment_id
                        item.treatment = state['treatment']
                        item.sensor_valid = state['sensor_valid']
                        item.soil_index = state['soil_index'] if state['sensor_valid'] else None
                        item.reservoir_mass_g = device.reservoir_ml
                        # Monitor compartilhado atribuído só ao vaso do ciclo ativo.
                        running = device.pump_on and device.unit_id == unit
                        item.pump_voltage_v = 6 if running else 0
                        item.pump_current_a = device.power_w/6 if running else 0
                        device.queue(topic+'telemetry', item.model_dump(mode='json'))
                    next_read = time.monotonic()+2
                pending = device.buffer[0] if device.buffer else None
            if pending and connected.is_set():
                published = client.publish(pending['topic'], json.dumps(pending['payload']), qos=1)
                published.wait_for_publish(timeout=3)
                if published.is_published():
                    with lock:
                        # Buffer pode ter sofrido overflow durante espera do broker.
                        if device.buffer and device.buffer[0] == pending:
                            device.acknowledge_buffer()
            time.sleep(.1)
    finally:
        with lock:
            device.disconnect()  # Nenhum novo comando inicia durante encerramento.
            enqueue(device.stop(datetime.now(timezone.utc)))
        flush_until = time.monotonic()+5
        while connected.is_set() and device.buffer and time.monotonic() < flush_until:
            pending = device.buffer[0]
            published = client.publish(pending['topic'], json.dumps(pending['payload']), qos=1)
            published.wait_for_publish(timeout=1)
            if not published.is_published():
                break
            device.acknowledge_buffer()
        client.disconnect()
        client.loop_stop()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--loop', action='store_true')
    parser.add_argument('--seconds', type=float, default=60)
    parser.add_argument('--state', default=None)
    parser.add_argument('--treatment', choices=['T1','T2','T3'], default='T1')
    parser.add_argument('--fallback', action='store_true', help='Histerese com parâmetros exclusivamente sintéticos')
    parser.add_argument('--three-units', action='store_true', help='Três vasos sintéticos no mesmo ESP32 e executor')
    args = parser.parse_args()
    if args.loop:
        run_loop(args.state, args.seconds, treatment=args.treatment, fallback_enabled=args.fallback, three_units=args.three_units)
    else:
        item = publish_fixture(sample())
        print(f'Fixture simulada enviada duas vezes: {item.message_id}. ACK MQTT não comprova persistência.')
