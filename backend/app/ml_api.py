import os
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException
from psycopg.types.json import Jsonb

from app.contracts import Prediction, StrictContract, Telemetry
from app.ml import infer, load_model
from app.store import connect

router = APIRouter(prefix='/ml', tags=['modelos'])


class PredictionRequest(StrictContract):
    device_id: str
    telemetry_message_id: UUID


@router.post('/predict')
def predict(item: PredictionRequest):
    try:
        with connect() as conn:
            row = conn.execute('SELECT payload FROM telemetry WHERE device_id=%s AND message_id=%s',
                               (item.device_id, item.telemetry_message_id)).fetchone()
            if not row:
                raise ValueError('Telemetria não encontrada')
            telemetry = Telemetry.model_validate(row['payload'])
            now = datetime.now(timezone.utc)
            if not 0 <= (now - telemetry.collected_at).total_seconds() <= 120:
                raise ValueError('Telemetria fora da janela de inferência')
            from app.store import resolve_configuration
            config=resolve_configuration(conn,telemetry.device_id,telemetry.unit_id,telemetry.experiment_id)
            artifact = load_model(config.model_path or os.environ.get('MODEL_PATH', '/models/active.json'))
            result = infer(artifact, telemetry.model_dump(mode='json'))
            prediction = Prediction(prediction_id=uuid4(), device_id=item.device_id,
                telemetry_message_id=item.telemetry_message_id, source=telemetry.source,
                predicted_at=now, valid_until=now + timedelta(seconds=60),
                soil_index=result, model_version=artifact['model_version'])
            conn.execute('INSERT INTO predictions(prediction_id,device_id,predicted_at,payload) VALUES(%s,%s,%s,%s)',
                         (prediction.prediction_id, item.device_id, now, Jsonb(prediction.model_dump(mode='json'))))
            return prediction
    except FileNotFoundError:
        raise HTTPException(status_code=409, detail='Modelo não disponível; treinar e selecionar artefato primeiro')
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
