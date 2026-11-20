"""Estatística descritiva do ensaio contínuo; quinzenas não são réplicas."""
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path


def timestamp(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Horário sem fuso')
    return result


def windows(plan):
    start, end = timestamp(plan['starts_at']), timestamp(plan['ends_at'])
    if end - start != timedelta(days=30) or plan['analysis_periods_are_replicates'] or plan['independent_units_per_treatment'] != 1:
        raise ValueError('Protocolo exige 30 dias contínuos, n=1 e períodos sem replicação')
    units = plan['units']
    if len(units) != 3 or len({u['unit_id'] for u in units}) != 3 or {u['treatment'] for u in units} != {'T1','T2','T3'}:
        raise ValueError('Exige três unidades/tratamentos distintos')
    return {'total30':(start,end), 'primeira15':(start,start+timedelta(days=15)), 'ultima15':(start+timedelta(days=15),end)}


def describe(plan, events, readings, as_of=None):
    as_of = as_of or datetime.now(timezone.utc)
    if as_of.tzinfo is None:
        raise ValueError('as_of sem fuso')
    intervals = windows(plan)
    units = {u['unit_id']:u['treatment'] for u in plan['units']}
    result = {'design':plan['design'], 'source':plan['source'], 'independent_units_per_treatment':1,
              'analysis_periods_are_replicates':False, 'inferential_claims':False, 'periods':{}}
    def compatible(row):
        return row.get('source') == plan['source'] and row.get('experiment_id') == plan['experiment_id'] and row.get('unit_id') in units and row.get('treatment') == units[row['unit_id']]
    unique_events = {}
    for event in events:
        if not compatible(event):
            continue
        if event['event_id'] in unique_events and unique_events[event['event_id']] != event:
            raise ValueError('event_id conflitante')
        unique_events[event['event_id']] = event
    cycles = {}
    for event in unique_events.values():
        cycles.setdefault(event['command_id'], []).append(event)
    for period, (start,end) in intervals.items():
        reports = {}
        elapsed = max(0, (min(as_of,end)-start).total_seconds())
        expected = math.ceil(elapsed / plan['sampling_interval_s'])
        for unit, treatment in units.items():
            observed = {r['message_id']:r for r in readings if compatible(r) and r['unit_id']==unit and start <= timestamp(r['collected_at']) < min(as_of,end)}
            valid = sum(r.get('sensor_valid') is True and r.get('soil_index') is not None for r in observed.values())
            covered_slots = {int((timestamp(r['collected_at'])-start).total_seconds()//plan['sampling_interval_s']) for r in observed.values() if r.get('sensor_valid') is True and r.get('soil_index') is not None}
            valid_cycles = []; unresolved = 0; invalid = 0
            for cycle in cycles.values():
                if cycle[0]['unit_id'] != unit:
                    continue
                terminals = [e for e in cycle if e['status'] in ('completed','failed')]
                if len(terminals) > 1:
                    raise ValueError('Mais de um resultado terminal no ciclo')
                terminal = terminals[0] if terminals else None
                # Ciclos completos pertencem ao período do resultado, sem contar duas vezes.
                anchor = timestamp(terminal['occurred_at'] if terminal else min(e['occurred_at'] for e in cycle))
                if not start <= anchor < min(as_of,end):
                    continue
                if not terminal:
                    if any(e['status'] in ('accepted','started','unconfirmed') for e in cycle):
                        unresolved += 1
                    continue
                # Água é o resultado principal; energia complementar não invalida a água medida.
                volume = terminal.get('volume_ml')
                if terminal.get('measurements_valid') is not True or volume is None or not math.isfinite(volume) or volume<0:
                    invalid += 1
                else:
                    valid_cycles.append(terminal)
            energy_cycles = [c for c in valid_cycles if isinstance(c.get('energy_wh'),(int,float)) and math.isfinite(c['energy_wh']) and c['energy_wh']>=0]
            reports[treatment] = dict(unit_id=unit, valid_cycles=len(valid_cycles), invalid_cycles=invalid,
                unresolved_cycles=unresolved, observed_valid_water_ml=sum(c['volume_ml'] for c in valid_cycles) if valid_cycles else None,
                observed_valid_energy_wh=sum(c['energy_wh'] for c in energy_cycles) if energy_cycles else None,
                cycles_without_energy=len(valid_cycles)-len(energy_cycles),
                valid_readings=valid, unique_readings=len(observed), expected_readings_until_snapshot=expected,
                valid_reading_coverage=len(covered_slots)/expected if expected else None,
                window_finished=as_of>=end, water_saving_pct=None)
        baseline = reports['T1']
        for treatment in ('T2','T3'):
            other = reports[treatment]
            eligible = as_of>=end and all(r['valid_cycles'] and not r['invalid_cycles'] and not r['unresolved_cycles'] and r['valid_reading_coverage']==1 for r in (baseline,other))
            if eligible and baseline['observed_valid_water_ml']>0:
                other['water_saving_pct']=100*(1-other['observed_valid_water_ml']/baseline['observed_valid_water_ml'])
        result['periods'][period]=reports
    return result


if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',default='/app/config/experiment.json')
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    from app.store import connect
    plan=json.loads(Path(args.plan).read_text(encoding='utf-8'))
    with connect() as conn:
        events=conn.execute("SELECT e.payload || jsonb_build_object('source',c.source,'experiment_id',c.payload->>'experiment_id','unit_id',c.payload->>'unit_id','treatment',c.payload->>'treatment') AS payload FROM command_events e JOIN commands c USING(command_id) WHERE c.source=%s AND c.payload->>'experiment_id'=%s UNION ALL SELECT payload FROM local_cycle_events WHERE payload->>'source'=%s AND payload->>'experiment_id'=%s",(plan['source'],plan['experiment_id'],plan['source'],plan['experiment_id'])).fetchall()
        readings=conn.execute('SELECT payload FROM telemetry WHERE source=%s AND experiment_id=%s',(plan['source'],plan['experiment_id'])).fetchall()
        # Estado derivado do ledger: identifica comandos sem resultado, sem inventar atuação.
        unresolved=conn.execute("SELECT payload,command_id FROM commands WHERE source=%s AND payload->>'experiment_id'=%s AND status NOT IN ('completed','failed','rejected')",(plan['source'],plan['experiment_id'])).fetchall()
    for row in unresolved:
        item=row['payload']
        events.append({'payload':dict(item,event_id='ledger-'+str(row['command_id']),command_id=str(row['command_id']),status='unconfirmed',occurred_at=item['issued_at'])})
    report=describe(plan,[e['payload'] for e in events],[r['payload'] for r in readings])
    path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as out:
        json.dump(report,out,ensure_ascii=False,indent=2,allow_nan=False)
    print('Relatório descritivo criado. Períodos não são réplicas; ausência não é consumo zero.')
