import logging
import os

import paho.mqtt.client as mqtt
import psycopg
from pydantic import ValidationError

from app.contracts import Telemetry, CommandEvent
from app.store import MessageConflict, save, save_event, migrate, save_rejection

logging.basicConfig(level=logging.INFO)
log = logging.getLogger('ingestion')


def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code.is_failure:
        raise RuntimeError('MQTT rejeitou conexão')
    client.subscribe([('irrigation/v1/+/telemetry', 1), ('irrigation/v1/+/events', 1)])
    log.info('MQTT conectado; assinatura solicitada')


def on_message(client, userdata, message):
    try:
        if len(message.payload) > 8192:
            raise ValueError('payload excede limite')
        is_event = message.topic.endswith('/events')
        item = (CommandEvent if is_event else Telemetry).model_validate_json(message.payload)
        suffix = 'events' if is_event else 'telemetry'
        if message.topic != f'irrigation/v1/{item.device_id}/{suffix}':
            raise ValueError('dispositivo diverge do tópico')
        inserted = save_event(item) if is_event else save(item)
        log.info('mensagem: %s', 'stored' if inserted else 'duplicate')
    except (ValidationError, ValueError, MessageConflict):
        log.warning('telemetria rejeitada no tópico %s', message.topic)
        try:
            save_rejection(message.topic, message.payload, 'invalid_contract_or_state')
        except psycopg.Error:
            log.error('Banco indisponível para registrar rejeição; sem ACK')
            os._exit(1)
    except psycopg.Error:
        # Encerrar sem ACK; a sessão persistente permite reentrega na reconexão.
        log.error('Banco indisponível; encerrando sem confirmar mensagem')
        os._exit(1)
    client.ack(message.mid, message.qos)


def make_client(client_id='tcc-ingestion-v1'):
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id,
                         clean_session=False, manual_ack=True)
    client.username_pw_set('backend', os.environ['MQTT_BACKEND_PASSWORD'])
    client.tls_set(ca_certs='/certs/ca.crt')
    client.on_connect = on_connect
    client.on_message = on_message
    client.reconnect_delay_set(1, 30)
    return client


if __name__ == '__main__':
    migrate()
    client = make_client()
    client.connect(os.environ.get('MQTT_HOST', 'mosquitto'), 8883, keepalive=30)
    client.loop_forever()
