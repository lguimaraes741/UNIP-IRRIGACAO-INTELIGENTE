"""Painel local da bancada: leituras ao vivo e testes guiados de pesagem.

Somente leitura do banco. Sessões de teste (etapas com horário do servidor) ficam em arquivos JSON
fora do Git; os resultados são calculados das contagens HX711 da telemetria, nunca gravados na placa.
"""
import json
import os
import time
import statistics
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator

from app.store import connect

DEVICE = os.environ.get('BENCH_DEVICE', 'esp32-01')
SESSIONS = Path(os.environ.get('BENCH_SESSIONS', '/sessions'))
REFILLS = Path(os.environ.get('BENCH_REFILLS', '/refills'))
PUMP_TESTS = Path(os.environ.get('BENCH_PUMP_TESTS', '/pump-tests'))
PULSE_MAX_MS = 2000  # firmware enforces the same cap, rest and dry-run budget
EXPERIMENT = os.environ.get('BENCH_EXPERIMENT', 'tcc-2026')
DOSE_ML = 50  # Common dose approved on 04/11 (protocol); the alert keeps three doses above the reserve.
REFILL_ALERT_DOSES = 3
PAGE = Path(__file__).with_name('bench_static') / 'index.html'
# Readings right after a load change are discarded: settling plus telemetry delivery delay.
SETTLE_S = 15
FLOW_SETTLE_S = 5
KINDS = ('calibration', 'withdrawal', 'press', 'flow')
SOIL = Path(os.environ.get('BENCH_SOIL', '/soil'))
WET_MAX_MS = 10000  # firmware: wet (submerged) pulses up to 10 s, 120 s per output per 10 min

app = FastAPI(title='TCC — painel de bancada', version='0.1.0')


def now():
    return datetime.now(timezone.utc)


def iso(value):
    return value.astimezone(timezone.utc).isoformat()


# ---------- análise (funções puras) ----------

def step_counts(step, samples, settle_s=SETTLE_S):
    """Contagens HX711 dentro da etapa, descartando o início (acomodação)."""
    start = datetime.fromisoformat(step['started_at']) + timedelta(seconds=settle_s)
    end = datetime.fromisoformat(step['ended_at'])
    return [s['counts'] for s in samples if start <= s['at'] <= end and s['counts'] is not None]


def summarize(steps, samples, settle_s=SETTLE_S):
    out = []
    for step in steps:
        if not step.get('ended_at'):
            continue
        counts = step_counts(step, samples, settle_s)
        out.append(dict(index=step['index'], load=step['load'], label=step['label'], n=len(counts), output=step.get('output'),
                        median=statistics.median(counts) if counts else None,
                        spread=(max(counts) - min(counts)) if counts else None))
    return out


def calibration(summary, mass_g):
    """Fator por etapa carregada contra a média das etapas vazias vizinhas (cancela deriva linear)."""
    factors = []
    for i, step in enumerate(summary):
        if step['load'] != 'loaded' or step['median'] is None:
            continue
        before = next((s for s in reversed(summary[:i]) if s['load'] == 'empty' and s['median'] is not None), None)
        after = next((s for s in summary[i + 1:] if s['load'] == 'empty' and s['median'] is not None), None)
        if not before or not after:
            continue
        base = (before['median'] + after['median']) / 2
        factors.append(dict(step=step['index'], delta_counts=step['median'] - base,
                            factor=(step['median'] - base) / mass_g, empty_drift_counts=after['median'] - before['median']))
    empties = [s for s in summary if s['load'] == 'empty' and s['median'] is not None]
    if not factors or not empties:
        return dict(ok=False, reason='Etapas válidas insuficientes (é preciso vazio antes e depois de cada carga).', factors=factors)
    values = [f['factor'] for f in factors]
    factor = statistics.mean(values)
    # Repeatability in grams of the reference mass across the loaded repetitions.
    spread_g = (max(values) - min(values)) * mass_g / abs(factor) if len(values) > 1 and factor else 0.0
    # The current platform reads ~20 counts/g; a factor far from that means the load was not applied.
    plausible = 2 <= abs(factor) <= 200
    return dict(ok=plausible, factor=factor, offset=empties[-1]['median'], repeats=len(values),
                repeatability_g=spread_g, factors=factors, mass_g=mass_g,
                reason=None if plausible else 'Fator implausível: confira se o peso foi colocado e retirado nas etapas indicadas.')


def withdrawal(summary, mass_g, factor):
    """Cada par com peso → sem peso é uma retirada; erro contra a massa de referência."""
    reps = []
    for i, step in enumerate(summary):
        if step['load'] != 'loaded' or step['median'] is None:
            continue
        nxt = next((s for s in summary[i + 1:] if s['load'] == 'empty'), None)
        if not nxt or nxt['median'] is None:
            continue
        removed = (step['median'] - nxt['median']) / factor
        reps.append(dict(step=step['index'], removed_g=removed, error_g=removed - mass_g))
    passed = sum(1 for r in reps if abs(r['error_g']) <= 2)
    return dict(ok=bool(reps), reps=reps, within_2g=passed, total=len(reps),
                approved=len(reps) >= 10 and passed >= 9, mass_g=mass_g, factor=factor)


def flow(summary, factor, pulse_ms, dose_ml=DOSE_ML):
    """Vazão por bomba: retirada do reservatório (antes − depois) ÷ tempo ligado.

    Cada grupo de pulsos seguidos da mesma bomba entre duas pesagens é uma repetição (1 ou mais
    pulsos). O primeiro grupo de cada bomba enche a mangueira (escorva) e fica fora da média.
    """
    by_output = {}
    i = 0
    while i < len(summary):
        step = summary[i]
        if step['load'] != 'pulse' or not step.get('output'):
            i += 1
            continue
        j = i
        while j + 1 < len(summary) and summary[j + 1]['load'] == 'pulse' and summary[j + 1].get('output') == step['output']:
            j += 1
        n = j - i + 1
        before = next((s for s in reversed(summary[:i]) if s['load'] == 'empty'), None)
        after = next((s for s in summary[j + 1:] if s['load'] == 'empty'), None)
        reps = by_output.setdefault(step['output'], [])
        ml = None
        if before and after and before['median'] is not None and after['median'] is not None:
            ml = (before['median'] - after['median']) / factor
        reps.append(dict(step=step['index'], pulses=n, ml=ml,
                         ml_per_s=ml / (n * pulse_ms / 1000) if ml is not None else None, priming=not reps))
        i = j + 1
    pumps = []
    for output, reps in sorted(by_output.items()):
        valid_reps = [r for r in reps if not r['priming'] and r['ml_per_s'] is not None]
        rates = [r['ml_per_s'] for r in valid_reps]
        mean = statistics.mean(rates) if rates else None
        spread = (max(rates) - min(rates)) / mean * 100 if rates and len(rates) > 1 and mean else None
        valid = bool(mean and mean > 0 and sum(r['pulses'] for r in valid_reps) >= 2)
        pumps.append(dict(output=output, reps=reps, ml_per_s=mean, spread_pct=spread, valid=valid,
                          dose_s=dose_ml / mean if valid else None))
    return dict(ok=bool(pumps) and all(p['valid'] for p in pumps), pumps=pumps, pulse_ms=pulse_ms, dose_ml=dose_ml, factor=factor)


def press(summary, factor):
    """Volta ao mesmo valor após apertar? Diferença de cada etapa solta contra a primeira base."""
    bases = [s for s in summary if s['load'] == 'empty' and s['median'] is not None]
    if len(bases) < 2:
        return dict(ok=False, returns=[])
    first = bases[0]['median']
    returns = [dict(step=s['index'], diff_g=(s['median'] - first) / factor) for s in bases[1:]]
    return dict(ok=True, returns=returns, max_abs_g=max(abs(r['diff_g']) for r in returns))


def reservoir_status(payload, dose_ml=DOSE_ML):
    """Estimativa do reservatório informada pela placa (reposição registrada − doses)."""
    ml = (payload or {}).get('reservoir_ml')
    if ml is None:
        return dict(known=False, level='unknown', message='Reservatório desconhecido: registre a reposição (volume total medido).')
    reserve = payload.get('reservoir_reserve_ml') or 0
    usable = max(0.0, ml - reserve)
    doses = int(usable // dose_ml)
    if usable < dose_ml:
        level, message = 'empty', 'Abaixo de uma dose: as bombas ficam bloqueadas. Complete o reservatório agora.'
    elif doses < REFILL_ALERT_DOSES:
        level, message = 'low', f'Restam ~{doses} dose(s) de {dose_ml:g} mL. Complete o reservatório.'
    else:
        level, message = 'ok', f'~{doses} doses de {dose_ml:g} mL disponíveis acima da reserva.'
    return dict(known=True, level=level, message=message, ml=ml, reserve_ml=reserve, usable_ml=usable,
                doses=doses, ref=payload.get('reservoir_ref'))


# ---------- dados ----------

def scale_samples(since):
    with connect() as conn:
        rows = conn.execute(
            """SELECT DISTINCT ON (collected_at) collected_at, (payload->>'scale_counts')::bigint AS counts,
                      (payload->>'reservoir_mass_g')::float AS mass, (payload->>'temperature_c')::float AS temp
               FROM telemetry WHERE device_id=%s AND source='physical' AND collected_at>=%s
               ORDER BY collected_at, unit_id""", (DEVICE, since)).fetchall()
    return [dict(at=r['collected_at'], counts=r['counts'], mass=r['mass'], temp=r['temp']) for r in rows]


def provisional_factor():
    """Fator gravado na placa, deduzido de duas leituras: massa = (contagens - zero) / fator."""
    with connect() as conn:
        rows = conn.execute(
            """SELECT DISTINCT (payload->>'scale_counts')::float AS c, (payload->>'reservoir_mass_g')::float AS m
               FROM telemetry WHERE device_id=%s AND source='physical' AND payload->>'scale_counts' IS NOT NULL
               AND payload->>'reservoir_mass_g' IS NOT NULL AND collected_at > now() - interval '1 hour'
               -- only readings under the board's current calibration; mixing two would give a bogus factor
               AND payload->>'scale_calibration_id' IS NOT DISTINCT FROM (
                   SELECT payload->>'scale_calibration_id' FROM telemetry WHERE device_id=%s AND source='physical'
                   ORDER BY collected_at DESC LIMIT 1)
               LIMIT 50""", (DEVICE, DEVICE)).fetchall()
    for a in rows:
        for b in rows:
            if abs(a['c'] - b['c']) >= 50 and a['m'] != b['m']:
                return (a['c'] - b['c']) / (a['m'] - b['m'])
    return None


@app.get('/')
def page():
    # no-store: an updated page must replace an open, cached copy on the next reload.
    return FileResponse(PAGE, headers={'Cache-Control': 'no-store'})


@app.get('/api/live')
def live(minutes: int = 15):
    minutes = max(1, min(minutes, 120))
    with connect() as conn:
        latest = conn.execute(
            """SELECT DISTINCT ON (unit_id) unit_id, treatment, collected_at, received_at, payload
               FROM telemetry WHERE device_id=%s AND source='physical' ORDER BY unit_id, collected_at DESC""", (DEVICE,)).fetchall()
    series = scale_samples(now() - timedelta(minutes=minutes))
    newest = max(latest, key=lambda r: r['collected_at'])['payload'] if latest else None
    return dict(server_time=iso(now()), device=DEVICE, reservoir=reservoir_status(newest),
                units=[dict(unit_id=r['unit_id'], treatment=r['treatment'], collected_at=iso(r['collected_at']),
                            age_s=(now() - r['received_at']).total_seconds(), payload=r['payload']) for r in latest],
                series=[dict(at=iso(s['at']), mass=s['mass'], counts=s['counts'], temp=s['temp']) for s in series],
                factor=provisional_factor(), calibration=latest_calibration())


class SessionStart(BaseModel):
    kind: str = Field(pattern='^(calibration|withdrawal|press|flow)$')
    pulse_ms: int | None = Field(default=None, ge=1000, le=WET_MAX_MS)
    mass_g: float | None = Field(default=None, gt=0, le=5000)
    factor: float | None = None
    note: str | None = Field(default=None, max_length=300)


class StepMark(BaseModel):
    index: int = Field(ge=0, le=200)
    load: str = Field(pattern='^(empty|loaded|press|pulse)$')
    output: int | None = Field(default=None, ge=1, le=3)
    label: str = Field(max_length=120)
    planned_s: int = Field(gt=0, le=1800)


def path_for(session_id):
    try:
        uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(404, 'Sessão inexistente')
    path = SESSIONS / f'{session_id}.json'
    if not path.exists():
        raise HTTPException(404, 'Sessão inexistente')
    return path


def load(session_id):
    return json.loads(path_for(session_id).read_text(encoding='utf-8'))


def store(session):
    SESSIONS.mkdir(parents=True, exist_ok=True)
    (SESSIONS / f"{session['id']}.json").write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding='utf-8')


def close_open_step(session, at):
    if session['steps'] and not session['steps'][-1].get('ended_at'):
        session['steps'][-1]['ended_at'] = iso(at)


def all_sessions():
    return [json.loads(p.read_text(encoding='utf-8')) for p in SESSIONS.glob('*.json')] if SESSIONS.exists() else []


def latest_calibration():
    done = [s for s in all_sessions() if s.get('calibration')]
    return max(done, key=lambda s: s['started_at'])['calibration'] if done else None


@app.post('/api/sessions')
def start(body: SessionStart):
    # A page reload leaves the previous session running; it can no longer be finished.
    for old in all_sessions():
        if old['status'] == 'running':
            old['status'] = 'abandoned'
            store(old)
    session = dict(id=str(uuid.uuid4()), kind=body.kind, mass_g=body.mass_g, factor=body.factor, note=body.note, pulse_ms=body.pulse_ms,
                   device=DEVICE, started_at=iso(now()), status='running', steps=[],
                   trial_eligible=False, purpose='diagnóstico/calibração da bancada; não é dado do ensaio')
    store(session)
    return session


@app.post('/api/sessions/{session_id}/steps')
def mark(session_id: str, body: StepMark):
    session = load(session_id)
    if session['status'] != 'running':
        raise HTTPException(409, 'Sessão encerrada')
    at = now()
    close_open_step(session, at)
    session['steps'].append(dict(index=body.index, load=body.load, label=body.label, planned_s=body.planned_s, started_at=iso(at), output=body.output))
    store(session)
    return dict(ok=True, started_at=iso(at))


@app.post('/api/sessions/{session_id}/finish')
def finish(session_id: str, cancelled: bool = False):
    session = load(session_id)
    if session['status'] == 'running':
        close_open_step(session, now())
        session['status'] = 'cancelled' if cancelled else 'completed'
        session['finished_at'] = iso(now())
        store(session)
    return session


@app.get('/api/sessions')
def sessions():
    items = all_sessions()
    items.sort(key=lambda s: s['started_at'], reverse=True)
    return [dict(id=s['id'], kind=s['kind'], status=s['status'], started_at=s['started_at'], mass_g=s.get('mass_g')) for s in items[:30]]


class Refill(BaseModel):
    total_ml: float = Field(ge=0, le=3000)  # 0 = reservatório vazio/removido
    reserve_ml: float = Field(ge=0, le=1000)
    note: str | None = Field(default=None, max_length=300)


def refill_message(body, refill_id, at):
    return dict(schema_version=1, refill_id=refill_id, device_id=DEVICE, experiment_id=EXPERIMENT,
                total_ml=body.total_ml, reserve_ml=body.reserve_ml, issued_at=iso(at), expires_at=iso(at + timedelta(minutes=5)))


def mqtt_client(tag):
    import paho.mqtt.client as mqtt
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f'tcc-bench-{tag}', clean_session=True)
    client.username_pw_set('backend', os.environ['MQTT_BACKEND_PASSWORD'])
    client.tls_set(ca_certs='/certs/ca.crt')
    return client


class Pulse(BaseModel):
    output: int = Field(ge=0, le=3)  # 0 = desligar todas
    duration_ms: int = Field(default=1000, ge=0, le=WET_MAX_MS)
    mode: str = Field(default='dry', pattern='^(dry|wet)$')  # wet = bomba submersa (vazão)

    @model_validator(mode='after')
    def dry_limit(self):
        if self.mode == 'dry' and self.duration_ms > PULSE_MAX_MS:
            raise ValueError('a seco, no máximo 2 s')
        return self


def pulse_message(body, test_id, at):
    return dict(schema_version=1, test_id=test_id, device_id=DEVICE, output=body.output, duration_ms=body.duration_ms, mode=body.mode,
                issued_at=iso(at), expires_at=iso(at + timedelta(seconds=30)))


@app.post('/api/pump')
def pump_pulse(body: Pulse):
    """Pulso manual (teste a seco): a placa aplica limites próprios e devolve o resultado medido."""
    import threading
    if body.output and body.duration_ms < 100:
        raise HTTPException(422, 'Duração mínima de 100 ms.')
    at, test_id = now(), str(uuid.uuid4())
    message, result, done = pulse_message(body, test_id, at), {}, threading.Event()

    def on_message(client, userdata, msg):
        try:
            data = json.loads(msg.payload)
        except ValueError:
            return
        if data.get('test_id') == test_id:
            result.update(data)
            done.set()
    client = mqtt_client(test_id[:8])
    client.on_message = on_message
    try:
        client.connect(os.environ.get('MQTT_HOST', 'mosquitto'), 8883, keepalive=30)
        client.loop_start()
        client.subscribe(f'irrigation/v1/{DEVICE}/manual_result', qos=1)  # before publishing
        time.sleep(0.3)
        info = client.publish(f'irrigation/v1/{DEVICE}/manual', json.dumps(message), qos=1)
        info.wait_for_publish(timeout=10)
        done.wait(timeout=body.duration_ms / 1000 + 25)  # result travels with the telemetry queue
    finally:
        client.loop_stop()
        client.disconnect()
    record = dict(request=message, result=result or None, recorded_at=iso(now()))
    PUMP_TESTS.mkdir(parents=True, exist_ok=True)
    (PUMP_TESTS / f'{at:%Y%m%dT%H%M%S}-{test_id}.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    if not result:
        raise HTTPException(504, 'A placa não respondeu; considere a bomba desligada só depois de conferir visualmente.')
    return result


@app.post('/api/reservoir')
def refill(body: Refill):
    """Registra a reposição: arquivo de intervenção + mensagem MQTT; a placa confirma pela telemetria."""
    if body.reserve_ml > body.total_ml or (body.total_ml > 0 and body.reserve_ml == body.total_ml):
        raise HTTPException(422, 'A reserva deve ser menor que o volume total.')
    import paho.mqtt.client as mqtt
    at, refill_id = now(), str(uuid.uuid4())
    message = refill_message(body, refill_id, at)
    REFILLS.mkdir(parents=True, exist_ok=True)
    record = dict(message, kind='refill', note=body.note, recorded_at=iso(at), confirmed=False)
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f'tcc-bench-{refill_id[:8]}', clean_session=True)
    client.username_pw_set('backend', os.environ['MQTT_BACKEND_PASSWORD'])
    client.tls_set(ca_certs='/certs/ca.crt')
    try:
        client.connect(os.environ.get('MQTT_HOST', 'mosquitto'), 8883, keepalive=30)
        client.loop_start()
        info = client.publish(f'irrigation/v1/{DEVICE}/reservoir', json.dumps(message), qos=1)
        info.wait_for_publish(timeout=10)
        published = info.is_published()
    except Exception as exc:  # noqa: BLE001 - reported to the page, nothing assumed delivered
        published, record['error'] = False, str(exc)[:200]
    finally:
        client.loop_stop()
        client.disconnect()
    record['published'] = published
    (REFILLS / f'{at:%Y%m%dT%H%M%S}-{refill_id}.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    if not published:
        raise HTTPException(502, 'Broker não confirmou a mensagem; a reposição não foi registrada na placa.')
    return dict(refill_id=refill_id, published=True)


def soil_raw_summary(unit_id, minutes=2):
    """Mediana do ADC bruto do vaso nos últimos minutos (leituras válidas da placa)."""
    with connect() as conn:
        rows = conn.execute(
            """SELECT (payload->>'soil_raw')::int AS raw FROM telemetry
               WHERE device_id=%s AND source='physical' AND unit_id=%s AND payload->>'soil_raw' IS NOT NULL
               AND collected_at > now() - make_interval(mins => %s)""", (DEVICE, unit_id, minutes)).fetchall()
    values = [r['raw'] for r in rows]
    return dict(n=len(values), median=statistics.median(values) if values else None,
                spread=(max(values) - min(values)) if values else None)


def soil_file():
    return SOIL / 'calibracao-solo.json'


def soil_state():
    path = soil_file()
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else dict(units={}, history=[])


class SoilPoint(BaseModel):
    unit_id: str = Field(pattern='^pot-0[1-3]$')
    state: str = Field(pattern='^(dry|wet)$')
    note: str | None = Field(default=None, max_length=300)


@app.get('/api/soil')
def soil_get():
    state = soil_state()
    for unit, points in state['units'].items():
        dry, wet = points.get('dry'), points.get('wet')
        points['ready'] = bool(dry and wet and abs(dry['median'] - wet['median']) >= 200)
    return state


@app.post('/api/soil')
def soil_record(body: SoilPoint):
    """Registra o ponto seco/saturado do vaso pela mediana dos últimos 2 min (não grava a placa)."""
    summary = soil_raw_summary(body.unit_id)
    if summary['n'] < 6:
        raise HTTPException(409, 'Leituras insuficientes nos últimos 2 min (mínimo 6).')
    if summary['spread'] > 150:
        raise HTTPException(409, f"Leitura instável (variação de {summary['spread']} no ADC); aguarde estabilizar.")
    state = soil_state()
    point = dict(median=summary['median'], n=summary['n'], spread=summary['spread'], at=iso(now()), note=body.note)
    state['units'].setdefault(body.unit_id, {})[body.state] = point
    state['history'].append(dict(unit_id=body.unit_id, state=body.state, **point))
    SOIL.mkdir(parents=True, exist_ok=True)
    soil_file().write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    return point


@app.get('/api/sessions/{session_id}/result')
def result(session_id: str):
    session = load(session_id)
    if not session['steps']:
        raise HTTPException(409, 'Sessão sem etapas')
    samples = scale_samples(datetime.fromisoformat(session['started_at']) - timedelta(seconds=30))
    # Water settles in seconds; the flow test uses short weighings (5 s discarded instead of 15 s).
    summary = summarize(session['steps'], samples, FLOW_SETTLE_S if session['kind'] == 'flow' else SETTLE_S)
    factor = session.get('factor') or provisional_factor()
    if session['kind'] == 'calibration':
        analysis = calibration(summary, session['mass_g'])
        if analysis['ok'] and session['status'] == 'completed' and not session.get('calibration'):
            session['calibration'] = dict(factor=analysis['factor'], offset=analysis['offset'],
                                          repeatability_g=analysis['repeatability_g'], computed_at=iso(now()))
            store(session)
    elif not factor:
        raise HTTPException(409, 'Fator indisponível')
    elif session['kind'] == 'withdrawal':
        analysis = withdrawal(summary, session['mass_g'], factor)
    elif session['kind'] == 'flow':
        analysis = flow(summary, factor, session.get('pulse_ms') or 5000)
    else:
        analysis = press(summary, factor)
    temps = [s['temp'] for s in samples if s['temp'] is not None and s['at'] >= datetime.fromisoformat(session['started_at'])]
    return dict(session=session, steps=summary, analysis=analysis,
                temperature=dict(min=min(temps), max=max(temps)) if temps else None)
