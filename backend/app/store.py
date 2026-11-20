import os

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.contracts import Telemetry


class MessageConflict(ValueError):
    pass


def connect():
    return psycopg.connect(os.environ['DATABASE_URL'], row_factory=dict_row)


def save(item: Telemetry) -> bool:
    payload = item.model_dump(mode='json')
    with connect() as conn:
        row = conn.execute(
            '''INSERT INTO telemetry (device_id, message_id, collected_at, experiment_id,
               unit_id, treatment, source, sensor_valid, soil_index, payload)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (device_id, message_id) DO NOTHING RETURNING message_id''',
            (item.device_id, item.message_id, item.collected_at, item.experiment_id,
             item.unit_id, item.treatment, item.source, item.sensor_valid, item.soil_index,
             Jsonb(payload)),
        ).fetchone()
        if row:
            return True
        existing = conn.execute(
            'SELECT payload FROM telemetry WHERE device_id=%s AND message_id=%s',
            (item.device_id, item.message_id),
        ).fetchone()
        if existing['payload'] != payload:
            raise MessageConflict('Identificador reutilizado com outro conteúdo')
        return False

# Safe to run on an existing volume; PostgreSQL serializes concurrent migration calls.
def migrate():
    if os.environ.get('AUTO_MIGRATE', 'true') != 'true':
        return
    from pathlib import Path
    path = Path(os.environ.get('CONTROL_MIGRATION_PATH', '/app/migrations/003-control.sql'))
    if not path.exists():
        path = Path(__file__).resolve().parents[2] / 'infra/postgres/003-control.sql'
    with connect() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(41926012)')
        conn.execute(path.read_text(encoding='utf-8'))


def save_command(item):
    from datetime import datetime, timezone
    from app.control import source_enabled
    if not source_enabled(item.source):
        raise ValueError('Atuação bloqueada para esta origem; habilite explicitamente')
    payload = item.model_dump(mode='json')
    with connect() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (item.device_id,))
        existing = conn.execute('SELECT payload FROM commands WHERE command_id=%s FOR UPDATE', (item.command_id,)).fetchone()
        if existing:
            if existing['payload'] != payload:
                raise MessageConflict('command_id reutilizado com outro conteúdo')
            return False
        now = datetime.now(timezone.utc)
        if not item.issued_at <= now < item.expires_at:
            raise ValueError('Comando futuro ou vencido')
        # A per-device transactional lock prevents overlapping commands across API/worker.
        active = conn.execute("SELECT 1 FROM commands WHERE device_id=%s AND status IN ('queued','published','accepted','started') AND (expires_at>now() OR status='started')", (item.device_id,)).fetchone()
        if active:
            raise ValueError('Há um comando ativo para o dispositivo')
        from app.contracts import DecisionConfiguration
        from app.control import decide, configuration_key, hysteresis_state
        cfg = resolve_configuration(conn,item.device_id,item.unit_id,item.experiment_id)
        if (item.experiment_id,item.unit_id,item.treatment,item.source) != (cfg.experiment_id,cfg.unit_id,cfg.treatment,cfg.source):
            raise ValueError('Contexto diverge da configuração')
        if item.max_duration_s > cfg.max_duration_s or item.target_volume_ml > cfg.target_volume_ml:
            raise ValueError('Comando excede limites configurados')
        tr = conn.execute('SELECT payload FROM telemetry WHERE device_id=%s AND experiment_id=%s AND source=%s AND unit_id=%s ORDER BY collected_at DESC LIMIT 1', (item.device_id,item.experiment_id,item.source,item.unit_id)).fetchone()
        telemetry = Telemetry.model_validate(tr['payload']) if tr else None
        # Manual simulation commands still obey sensor/reservoir/calibration/cooldown guards.
        last = conn.execute("SELECT issued_at FROM commands WHERE device_id=%s AND payload->>'experiment_id'=%s AND payload->>'unit_id'=%s AND status NOT IN ('rejected','expired') ORDER BY issued_at DESC LIMIT 1", (item.device_id,item.experiment_id,item.unit_id)).fetchone()
        allowed, reason = decide(cfg,telemetry,now,last['issued_at'] if last else None,simulation_enabled=True,daily_volume_ml=daily_volume(conn,cfg,now))
        if reason in ('control_disabled','daily_volume_cap','missing_telemetry','telemetry_context_mismatch','stale_telemetry','invalid_or_uncalibrated_sensor','insufficient_or_unknown_reservoir','cooldown','soil_at_or_above_high'):
            raise ValueError(reason)
        conn.execute('INSERT INTO commands(command_id,device_id,source,issued_at,expires_at,payload) VALUES(%s,%s,%s,%s,%s,%s)', (item.command_id,item.device_id,item.source,item.issued_at,item.expires_at,Jsonb(payload)))
        conn.execute('INSERT INTO command_outbox(command_id) VALUES(%s)', (item.command_id,))
        return True


TRANSITIONS = {'queued': {'accepted','rejected'}, 'published': {'accepted','rejected'}, 'accepted': {'started','failed'}, 'started': {'completed','failed'}, 'completed': set(), 'rejected': set(), 'failed': set(), 'expired': set()}


def save_event(item):
    if item.origin == 'local':
        return save_local_event(item)
    payload = item.model_dump(mode='json')
    with connect() as conn:
        command = conn.execute('SELECT * FROM commands WHERE command_id=%s FOR UPDATE', (item.command_id,)).fetchone()
        if not command:
            raise ValueError('Comando desconhecido')
        if item.status in ('completed','failed') and item.measurements_valid:
            interference=conn.execute("SELECT 1 FROM interventions WHERE device_id=%s AND (payload->>'invalidates_measurements')::boolean AND started_at<=%s AND (ended_at IS NULL OR ended_at>=%s) LIMIT 1",(item.device_id,item.occurred_at,command['issued_at'])).fetchone()
            if interference:
                payload.update(measurements_valid=False,volume_ml=None,energy_wh=None)
                payload['reason']='intervention_invalidated_measurements'
        previous = conn.execute('SELECT payload FROM command_events WHERE event_id=%s', (item.event_id,)).fetchone()
        if previous:
            from app.contracts import CommandEvent
            if CommandEvent.model_validate(previous['payload']).model_dump(mode='json') != payload:
                raise MessageConflict('event_id reutilizado')
            return False
        if item.device_id != command['device_id'] or item.source != command['source'] or (item.unit_id is not None and item.unit_id!=command['payload']['unit_id']) or (item.experiment_id is not None and item.experiment_id!=command['payload']['experiment_id']) or (item.treatment is not None and item.treatment!=command['payload']['treatment']):
            raise ValueError('Evento diverge do comando')
        latest = conn.execute('SELECT occurred_at FROM command_events WHERE command_id=%s ORDER BY occurred_at DESC LIMIT 1', (item.command_id,)).fetchone()
        if item.occurred_at < command['issued_at'] or (latest and item.occurred_at < latest['occurred_at']):
            raise ValueError('Evento fora de ordem temporal')
        if item.status not in TRANSITIONS[command['status']]:
            raise ValueError('Transição inválida')
        if item.status in ('accepted','started') and item.occurred_at >= command['expires_at']:
            raise ValueError('Comando vencido')
        if item.duration_s is not None and item.duration_s > command['payload']['max_duration_s'] + 1:
            raise ValueError('Duração excede limite do comando')
        conn.execute('INSERT INTO command_events(event_id,command_id,occurred_at,status,payload) VALUES(%s,%s,%s,%s,%s)', (item.event_id,item.command_id,item.occurred_at,item.status,Jsonb(payload)))
        conn.execute('UPDATE commands SET status=%s WHERE command_id=%s', (item.status,item.command_id))
        return True


def save_local_event(item):
    """Ledger auditado de ciclos autônomos; não cria outbox/comando remoto."""
    # Local cycles are the board's own fallback: always recorded (they happened), never commanded here.
    payload = item.model_dump(mode='json')
    with connect() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (str(item.command_id),))
        old = conn.execute('SELECT payload FROM local_cycle_events WHERE event_id=%s', (item.event_id,)).fetchone()
        if old:
            if old['payload'] != payload:
                raise MessageConflict('event_id local reutilizado')
            return False
        if conn.execute('SELECT 1 FROM commands WHERE command_id=%s', (item.command_id,)).fetchone():
            raise ValueError('Ciclo local não pode reutilizar comando remoto')
        previous = conn.execute('SELECT status,occurred_at,payload FROM local_cycle_events WHERE command_id=%s ORDER BY received_at DESC LIMIT 1', (item.command_id,)).fetchone()
        if previous:
            original = previous['payload']
            if any(payload[k] != original[k] for k in ('device_id','experiment_id','unit_id','source')):
                raise ValueError('Contexto local divergente')
            if item.occurred_at < previous['occurred_at'] or item.status not in TRANSITIONS[previous['status']]:
                raise ValueError('Evento local fora de ordem/transição')
        elif item.status not in ('accepted','rejected'):
            raise ValueError('Ciclo local sem evento inicial')
        conn.execute('INSERT INTO local_cycle_events(event_id,command_id,device_id,occurred_at,status,payload) VALUES(%s,%s,%s,%s,%s,%s)',
                     (item.event_id,item.command_id,item.device_id,item.occurred_at,item.status,Jsonb(payload)))
        return True


def save_rejection(topic, payload, reason):
    with connect() as conn:
        conn.execute('INSERT INTO rejected_messages(topic,reason,payload) VALUES(%s,%s,%s)',
                     (topic[:256], reason[:300], payload[:8192]))


def evaluate(device_id, request_id, unit_id=None, experiment_id=None):
    from datetime import datetime, timedelta, timezone
    from uuid import uuid4
    from app.contracts import DecisionConfiguration, IrrigationCommand, Prediction
    from app.control import decide, configuration_key, hysteresis_state
    with connect() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (device_id,))
        old = conn.execute('SELECT payload FROM decisions WHERE request_id=%s', (request_id,)).fetchone()
        if old:
            if old['payload']['device_id'] != device_id or (unit_id is not None and old['payload'].get('unit_id') != unit_id) or (experiment_id is not None and old['payload'].get('experiment_id') != experiment_id):
                raise MessageConflict('request_id reutilizado para outro dispositivo')
            return old['payload']
        config = resolve_configuration(conn,device_id,unit_id,experiment_id)
        unit_id,experiment_id=config.unit_id,config.experiment_id
        row = conn.execute('SELECT payload FROM telemetry WHERE device_id=%s AND experiment_id=%s AND source=%s AND unit_id=%s ORDER BY collected_at DESC LIMIT 1', (device_id,config.experiment_id,config.source,config.unit_id)).fetchone()
        telemetry = Telemetry.model_validate(row['payload']) if row else None
        row = conn.execute("SELECT p.payload FROM predictions p JOIN telemetry t ON t.device_id=p.device_id AND t.message_id=(p.payload->>'telemetry_message_id')::uuid WHERE p.device_id=%s AND t.experiment_id=%s AND t.unit_id=%s AND t.source=%s ORDER BY p.predicted_at DESC LIMIT 1", (device_id,config.experiment_id,config.unit_id,config.source)).fetchone()
        prediction = Prediction.model_validate(row['payload']) if row else None
        previous = conn.execute("SELECT issued_at FROM commands WHERE device_id=%s AND payload->>'experiment_id'=%s AND payload->>'unit_id'=%s AND status NOT IN ('rejected','expired') ORDER BY issued_at DESC LIMIT 1", (device_id,config.experiment_id,config.unit_id)).fetchone()
        active = conn.execute("SELECT 1 FROM commands WHERE device_id=%s AND payload->>'experiment_id'=%s AND payload->>'unit_id'=%s AND (status IN ('published','accepted','started') OR (status='queued' AND expires_at>now()))", (device_id,config.experiment_id,config.unit_id)).fetchone()
        now = datetime.now(timezone.utc)
        config_key = configuration_key(config)
        last_decision = conn.execute("SELECT payload FROM decisions WHERE device_id=%s AND payload->>'experiment_id'=%s AND payload->>'unit_id'=%s ORDER BY created_at DESC LIMIT 1", (device_id,config.experiment_id,config.unit_id)).fetchone()
        prior_latch = bool(last_decision and last_decision['payload'].get('configuration_key') == config_key and last_decision['payload'].get('hysteresis_latched', False))
        # Stale data cannot change state; valid high readings release the latch even during cooldown.
        fresh = telemetry is not None and 0 <= (now-telemetry.collected_at).total_seconds() <= config.max_telemetry_age_s
        latch = hysteresis_state(config,telemetry,prior_latch) if fresh else prior_latch
        from app.control import source_enabled
        act, reason = decide(config,telemetry,now,previous['issued_at'] if previous else None,prediction,bool(active),source_enabled(config.source), latch, daily_volume(conn,config,now))
        if act and config.treatment == 'T3' and reason == 'prediction_below_low':
            latch = True  # anticipated start keeps dosing (interval permitting) until observed >= soil_high
        result = {'request_id': str(request_id), 'device_id':device_id, 'experiment_id':config.experiment_id,'unit_id':config.unit_id, 'reason':reason, 'actuate':act, 'telemetry_message_id':str(telemetry.message_id) if telemetry else None, 'prediction_id':str(prediction.prediction_id) if prediction else None, 'command_id':None, 'configuration_key':config_key, 'hysteresis_latched':latch}
        if act:
            command = IrrigationCommand(command_id=uuid4(),decision_id=request_id,device_id=device_id,experiment_id=config.experiment_id,unit_id=config.unit_id,treatment=config.treatment,source=config.source,issued_at=now,expires_at=now+timedelta(seconds=60),target_volume_ml=config.target_volume_ml,max_duration_s=config.max_duration_s,reason=reason,model_version=prediction.model_version if prediction and config.treatment=='T3' and reason=='prediction_below_low' else None)
            conn.execute('INSERT INTO commands(command_id,device_id,source,issued_at,expires_at,payload) VALUES(%s,%s,%s,%s,%s,%s)', (command.command_id,device_id,command.source,now,command.expires_at,Jsonb(command.model_dump(mode='json'))))
            conn.execute('INSERT INTO command_outbox(command_id) VALUES(%s)', (command.command_id,))
            result['command_id'] = str(command.command_id)
        conn.execute('INSERT INTO decisions(request_id,device_id,payload) VALUES(%s,%s,%s)', (request_id,device_id,Jsonb(result)))
        return result


def daily_volume(conn, config, now):
    """Água planejada no vaso nas últimas 24 h: doses remotas não recusadas/vencidas + ciclos locais iniciados.

    Conta a dose comandada (não a medida): falta de medição não pode liberar mais água.
    """
    row=conn.execute("""SELECT coalesce(sum((payload->>'target_volume_ml')::float),0) AS ml FROM commands
        WHERE device_id=%s AND payload->>'experiment_id'=%s AND payload->>'unit_id'=%s
        AND status NOT IN ('rejected','expired') AND issued_at > %s - interval '24 hours'""",
        (config.device_id,config.experiment_id,config.unit_id,now)).fetchone()
    local=conn.execute("""SELECT count(DISTINCT command_id) AS n FROM local_cycle_events
        WHERE device_id=%s AND payload->>'unit_id'=%s AND status='started' AND occurred_at > %s - interval '24 hours'""",
        (config.device_id,config.unit_id,now)).fetchone()
    return float(row['ml'])+local['n']*config.target_volume_ml


def resolve_configuration(conn, device_id, unit_id=None, experiment_id=None):
    from app.contracts import DecisionConfiguration
    rows=conn.execute("SELECT payload FROM control_configs WHERE device_id=%s AND (%s::text IS NULL OR payload->>'unit_id'=%s) AND (%s::text IS NULL OR payload->>'experiment_id'=%s)",(device_id,unit_id,unit_id,experiment_id,experiment_id)).fetchall()
    if not rows:
        raise ValueError('Configuração ausente')
    if len(rows)!=1:
        raise ValueError('Contexto ambíguo; informe experiment_id e unit_id')
    return DecisionConfiguration.model_validate(rows[0]['payload'])


def device_barrier(conn, device_id, now):
    # Once delivered/accepted/started, only a terminal device event can release the pump.
    if conn.execute("SELECT 1 FROM commands WHERE device_id=%s AND status IN ('published','accepted','started') LIMIT 1",(device_id,)).fetchone():
        return 'active_command'
    if conn.execute("SELECT 1 FROM interventions WHERE device_id=%s AND status='started' LIMIT 1",(device_id,)).fetchone():
        return 'intervention_active'
    local=conn.execute("SELECT DISTINCT ON(command_id) status FROM local_cycle_events WHERE device_id=%s ORDER BY command_id,received_at DESC",(device_id,)).fetchall()
    if any(row['status'] in ('accepted','started') for row in local):
        return 'local_cycle_active'
    last=conn.execute("SELECT max(e.received_at) AS at FROM command_events e JOIN commands c USING(command_id) WHERE c.device_id=%s AND e.status IN ('completed','failed')",(device_id,)).fetchone()
    cfgs=conn.execute('SELECT payload FROM control_configs WHERE device_id=%s',(device_id,)).fetchall()
    settle=max((row['payload'].get('stabilization_s',0) for row in cfgs),default=0)
    if last and last['at'] and (now-last['at']).total_seconds()<settle:
        return 'awaiting_stabilization'
    return None


def revalidate_queued(conn, command, now):
    from app.contracts import DecisionConfiguration, Prediction
    from app.control import decide
    cfg=resolve_configuration(conn,command['device_id'],command['unit_id'],command['experiment_id'])
    from app.control import source_enabled
    if not source_enabled(command['source']):
        return 'physical_actuation_disabled' if command['source']=='physical' else 'control_disabled'
    if command['max_duration_s']>cfg.max_duration_s or command['target_volume_ml']>cfg.target_volume_ml:
        return 'configuration_limits_changed'
    row=conn.execute('SELECT payload FROM telemetry WHERE device_id=%s AND experiment_id=%s AND unit_id=%s AND source=%s ORDER BY collected_at DESC LIMIT 1',(cfg.device_id,cfg.experiment_id,cfg.unit_id,cfg.source)).fetchone()
    reading=Telemetry.model_validate(row['payload']) if row else None
    # The queued request must still be safe. Predictions are tied to its current unit/readings.
    row=conn.execute("SELECT p.payload FROM predictions p JOIN telemetry t ON t.device_id=p.device_id AND t.message_id=(p.payload->>'telemetry_message_id')::uuid WHERE p.device_id=%s AND t.experiment_id=%s AND t.unit_id=%s ORDER BY p.predicted_at DESC LIMIT 1",(cfg.device_id,cfg.experiment_id,cfg.unit_id)).fetchone()
    prediction=Prediction.model_validate(row['payload']) if row else None
    previous=conn.execute("SELECT issued_at FROM commands WHERE device_id=%s AND payload->>'experiment_id'=%s AND payload->>'unit_id'=%s AND command_id<>%s AND status NOT IN ('rejected','expired','queued') ORDER BY issued_at DESC LIMIT 1",(cfg.device_id,cfg.experiment_id,cfg.unit_id,command['command_id'])).fetchone()
    decision=conn.execute('SELECT payload FROM decisions WHERE request_id=%s',(command.get('decision_id'),)).fetchone() if command.get('decision_id') else None
    if decision:
        from app.control import configuration_key
        if decision['payload'].get('configuration_key') != configuration_key(cfg):
            return 'configuration_changed_after_decision'
        if command.get('model_version') and (not prediction or prediction.model_version != command['model_version']):
            return 'model_changed_after_decision'
    latch=bool(decision and decision['payload'].get('hysteresis_latched'))
    allowed,reason=decide(cfg,reading,now,previous['issued_at'] if previous else None,prediction,False,True,latch,daily_volume(conn,cfg,now)-cfg.target_volume_ml)
    # Manual commands retain legacy policy override but never override safety guards.
    safety={'control_disabled','daily_volume_cap','missing_telemetry','telemetry_context_mismatch','stale_telemetry','invalid_or_uncalibrated_sensor','insufficient_or_unknown_reservoir','cooldown','soil_at_or_above_high'}
    if reason in safety or (command.get('decision_id') and not allowed):
        return reason
    return None


def save_intervention(item):
    payload=item.model_dump(mode='json')
    with connect() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',(item.device_id,))
        cfg=resolve_configuration(conn,item.device_id,item.unit_id,item.experiment_id)
        if cfg.source!=item.source:
            raise ValueError('Intervenção diverge da origem configurada')
        old=conn.execute('SELECT * FROM interventions WHERE intervention_id=%s FOR UPDATE',(item.intervention_id,)).fetchone()
        if old:
            if old['payload']==payload:
                return False
            if item.status!='completed' or old['status']!='started' or any(old['payload'][k]!=payload[k] for k in ('device_id','experiment_id','unit_id','kind','source')) or item.occurred_at<old['started_at']:
                raise MessageConflict('Intervenção inconsistente')
            conn.execute('UPDATE interventions SET status=%s,ended_at=%s,payload=%s WHERE intervention_id=%s',(item.status,item.occurred_at,Jsonb(payload),item.intervention_id))
        else:
            if item.status!='started':
                raise ValueError('Intervenção exige registro inicial')
            conn.execute('INSERT INTO interventions VALUES(%s,%s,%s,%s,%s,%s,%s,NULL,%s)',(item.intervention_id,item.device_id,item.experiment_id,item.unit_id,item.kind,item.status,item.occurred_at,Jsonb(payload)))
        return True
