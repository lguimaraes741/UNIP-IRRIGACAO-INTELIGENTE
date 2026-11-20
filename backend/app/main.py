import os
import secrets

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.contracts import Telemetry
from app.store import MessageConflict, connect, save

app = FastAPI(title='TCC Irrigação — base de integração', version='0.1.0')
bearer = HTTPBearer()


def authorize(credentials: HTTPAuthorizationCredentials = Depends(bearer)):
    if not secrets.compare_digest(credentials.credentials, os.environ['API_TOKEN']):
        raise HTTPException(status_code=401, detail='Credencial inválida')


@app.get('/health')
def health():
    try:
        with connect() as conn:
            conn.execute('SELECT 1 FROM telemetry LIMIT 1')
        return {'status': 'ready', 'actuation_enabled': False}
    except psycopg.Error:
        raise HTTPException(status_code=503, detail='Banco indisponível')


@app.post('/telemetry', dependencies=[Depends(authorize)])
def ingest(item: Telemetry):
    try:
        inserted = save(item)
        return {'status': 'stored' if inserted else 'duplicate'}
    except MessageConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except psycopg.Error:
        raise HTTPException(status_code=503, detail='Persistência indisponível; reenviar')


@app.get('/telemetry', dependencies=[Depends(authorize)])
def latest(experiment_id: str = Query(min_length=1, max_length=48),
           source: str = Query(pattern='^(physical|simulation)$'),
           limit: int = Query(default=50, ge=1, le=500)):
    with connect() as conn:
        rows = conn.execute(
            '''SELECT payload, received_at FROM telemetry
               WHERE experiment_id=%s AND source=%s
               ORDER BY received_at DESC LIMIT %s''', (experiment_id, source, limit),
        ).fetchall()
    return rows

from uuid import UUID
from app.contracts import CommandEvent, DecisionConfiguration, IrrigationCommand, Prediction, StrictContract
from app.store import evaluate, migrate, save_command, save_event
from psycopg.types.json import Jsonb


@app.on_event('startup')
def initialize_control():
    migrate()


def persist_result(operation):
    try:
        return operation()
    except MessageConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except psycopg.Error:
        raise HTTPException(status_code=503, detail='Persistência indisponível; reenviar')


@app.post('/commands', dependencies=[Depends(authorize)])
def command_create(item: IrrigationCommand):
    return persist_result(lambda: {'status':'stored' if save_command(item) else 'duplicate'})


@app.post('/events', dependencies=[Depends(authorize)])
def event_create(item: CommandEvent):
    return persist_result(lambda: {'status':'stored' if save_event(item) else 'duplicate'})


@app.get('/commands', dependencies=[Depends(authorize)])
def command_list(device_id: str, limit: int = Query(default=50,ge=1,le=500),unit_id: str | None=None,experiment_id: str | None=None):
    with connect() as conn:
        return conn.execute("SELECT payload,status FROM commands WHERE device_id=%s AND (%s::text IS NULL OR payload->>'unit_id'=%s) AND (%s::text IS NULL OR payload->>'experiment_id'=%s) ORDER BY issued_at DESC LIMIT %s",(device_id,unit_id,unit_id,experiment_id,experiment_id,limit)).fetchall()


@app.post('/control/config', dependencies=[Depends(authorize)])
def configure(item: DecisionConfiguration):
    with connect() as conn:
        conn.execute("INSERT INTO control_configs(device_id,payload) VALUES(%s,%s) ON CONFLICT(device_id,(payload->>'experiment_id'),(payload->>'unit_id')) DO UPDATE SET payload=excluded.payload,updated_at=now()", (item.device_id,Jsonb(item.model_dump(mode='json'))))
    from app.control import source_enabled
    return {'status':'stored','physical_actuation_enabled':item.source=='physical' and source_enabled('physical')}


class EvaluationRequest(StrictContract):
    device_id: str
    request_id: UUID
    unit_id: str | None = None
    experiment_id: str | None = None


@app.post('/control/evaluate', dependencies=[Depends(authorize)])
def control_evaluate(item: EvaluationRequest):
    return persist_result(lambda: evaluate(item.device_id,item.request_id,item.unit_id,item.experiment_id))


@app.post('/predictions', dependencies=[Depends(authorize)])
def prediction_create(item: Prediction):
    def operation():
        from datetime import datetime, timezone
        if item.valid_until <= datetime.now(timezone.utc):
            raise ValueError('Previsão vencida')
        with connect() as conn:
            telemetry = conn.execute('SELECT source FROM telemetry WHERE device_id=%s AND message_id=%s', (item.device_id,item.telemetry_message_id)).fetchone()
            if not telemetry or telemetry['source'] != item.source:
                raise ValueError('Previsão sem telemetria compatível')
            payload = item.model_dump(mode='json')
            row = conn.execute('INSERT INTO predictions(prediction_id,device_id,predicted_at,payload) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING prediction_id',(item.prediction_id,item.device_id,item.predicted_at,Jsonb(payload))).fetchone()
            if not row:
                old = conn.execute('SELECT payload FROM predictions WHERE prediction_id=%s',(item.prediction_id,)).fetchone()
                if old['payload'] != payload:
                    raise MessageConflict('prediction_id reutilizado')
            return {'status':'stored' if row else 'duplicate'}
    return persist_result(operation)

@app.get('/events', dependencies=[Depends(authorize)])
def event_list(command_id: UUID, limit: int = Query(default=50,ge=1,le=500)):
    with connect() as conn:
        return conn.execute('SELECT payload FROM command_events WHERE command_id=%s ORDER BY occurred_at LIMIT %s',(command_id,limit)).fetchall()

from app.ml_api import router as ml_router
app.include_router(ml_router, dependencies=[Depends(authorize)])


@app.get('/local-cycles/events', dependencies=[Depends(authorize)])
def local_events(device_id: str, limit: int = Query(default=50, ge=1, le=500)):
    with connect() as conn:
        return conn.execute('SELECT payload FROM local_cycle_events WHERE device_id=%s ORDER BY received_at DESC LIMIT %s',
                            (device_id, limit)).fetchall()


@app.get('/rejections', dependencies=[Depends(authorize)])
def rejections(device_id: str = Query(pattern=r'^[a-z0-9][a-z0-9-]{0,47}$'),
               limit: int = Query(default=50, ge=1, le=500)):
    with connect() as conn:
        return conn.execute('SELECT topic,received_at,reason FROM rejected_messages WHERE topic LIKE %s ORDER BY received_at DESC LIMIT %s',
                            (f'irrigation/v1/{device_id}/%', limit)).fetchall()


from app.contracts import Intervention
from app.store import save_intervention

@app.post('/interventions',dependencies=[Depends(authorize)])
def intervention_create(item: Intervention):
    return persist_result(lambda: {'status':'stored' if save_intervention(item) else 'duplicate'})

@app.get('/interventions',dependencies=[Depends(authorize)])
def intervention_list(device_id: str,unit_id: str | None=None):
    with connect() as conn:
        return conn.execute("SELECT payload FROM interventions WHERE device_id=%s AND (%s::text IS NULL OR unit_id=%s) ORDER BY started_at DESC LIMIT 100",(device_id,unit_id,unit_id)).fetchall()
