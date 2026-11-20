"""Três contextos SIMULADOS, fila e medição compartilhada. Sem resultado agronômico."""
import os
import threading
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import psycopg

from control_integration_check import wait_for
from simulate import run_loop, sample


def main():
    stop = threading.Event()
    started = datetime.now(timezone.utc)
    thread = threading.Thread(target=run_loop, kwargs=dict(seconds=50, stop_event=stop, three_units=True), daemon=True)
    thread.start()
    headers = {'Authorization':'Bearer '+os.environ['API_TOKEN']}
    configs = []
    with httpx.Client(base_url='http://backend:8000',headers=headers,timeout=10) as api:
        try:
            def readings():
                rows=api.get('/telemetry',params=dict(experiment_id='sim-three-pots',source='simulation')).json()
                return {row['payload']['unit_id']:row['payload'] for row in reversed(rows)}
            def current_readings(after):
                rows=readings()
                return rows if len(rows)==3 and all(datetime.fromisoformat(row['collected_at']) >= after for row in rows.values()) else None
            latest=wait_for(lambda:current_readings(started))
            for index in range(1,4):
                config=dict(device_id='sim-01',experiment_id='sim-three-pots',unit_id=f'sim-pot-0{index}',
                            treatment=f'T{index}',source='simulation',enabled=True,calibration_id='sim-fixture-v1',
                            soil_low=30,soil_high=60,min_reservoir_mass_g=100,target_volume_ml=5+5*index,
                            max_duration_s=10,max_telemetry_age_s=10,cooldown_s=1,stabilization_s=.5,
                            schedule_interval_s=1 if index==1 else None)
                api.post('/control/config',json=config).raise_for_status()
                configs.append(config)
            ambiguous=api.post('/control/evaluate',json=dict(device_id='sim-01',request_id=str(uuid4())))
            assert ambiguous.status_code==422,ambiguous.text
            # Reset auditável da histerese de T2 em repetições; nunca apagar histórico.
            high=sample()
            high.experiment_id='sim-three-pots'
            high.unit_id='sim-pot-02'
            high.treatment='T2'
            high.soil_index=80
            api.post('/telemetry',json=high.model_dump(mode='json')).raise_for_status()
            reset=api.post('/control/evaluate',json=dict(device_id='sim-01',experiment_id='sim-three-pots',
                unit_id='sim-pot-02',request_id=str(uuid4())))
            reset.raise_for_status()
            assert not reset.json()['actuate'] and reset.json()['reason']=='soil_at_or_above_high',reset.text
            wait_for(lambda:current_readings(high.collected_at+timedelta(microseconds=1)))
            # Executar T3 primeiro: forecast fica ligado à leitura mais recente na publicação.
            # As próximas leituras continuam renovando segurança de T1/T2 enfileirados.
            latest=readings()
            now=datetime.now(timezone.utc)
            prediction=dict(prediction_id=str(uuid4()),device_id='sim-01',
                            telemetry_message_id=latest['sim-pot-03']['message_id'],source='simulation',
                            predicted_at=now.isoformat(),valid_until=(now+timedelta(seconds=30)).isoformat(),
                            horizon_s=1800,soil_index=20,model_version='sim-three-fixture-not-trained')
            api.post('/predictions',json=prediction).raise_for_status()
            commands=[]
            for config in [configs[2],configs[0],configs[1]]:
                request=dict(device_id='sim-01',experiment_id='sim-three-pots',unit_id=config['unit_id'],request_id=str(uuid4()))
                response=api.post('/control/evaluate',json=request)
                response.raise_for_status()
                result=response.json()
                assert result['actuate'],result
                assert api.post('/control/evaluate',json=request).json()==result
                commands.append(result['command_id'])
            def completed():
                rows=api.get('/commands',params=dict(device_id='sim-01',experiment_id='sim-three-pots')).json()
                statuses={row['payload']['command_id']:row['status'] for row in rows}
                return all(statuses.get(identifier)=='completed' for identifier in commands)
            wait_for(completed,timeout=35)
            with psycopg.connect(os.environ['DATABASE_URL']) as conn:
                rows=conn.execute('SELECT payload FROM command_events WHERE command_id=ANY(%s::uuid[]) ORDER BY occurred_at', (commands,)).fetchall()
            events=[row[0] for row in rows]
            for identifier in commands:
                cycle=[event for event in events if event['command_id']==identifier]
                assert [event['status'] for event in cycle]==['accepted','started','completed'],cycle
                assert cycle[-1]['measurements_valid']
            intervals=[]
            for identifier in commands:
                cycle=[event for event in events if event['command_id']==identifier]
                intervals.append((datetime.fromisoformat(cycle[1]['occurred_at']),datetime.fromisoformat(cycle[-1]['occurred_at'])))
            intervals.sort()
            assert all(intervals[i][1] <= intervals[i+1][0] for i in range(2)),intervals
            assert {event['volume_ml'] for event in events if event['status']=='completed'}=={10,15,20}
            assert {event['unit_id'] for event in events}=={'sim-pot-01','sim-pot-02','sim-pot-03'}
            wait_for(lambda: all(abs(row['reservoir_mass_g']-1455)<.01 for row in readings().values()))
            stop.set()
            thread.join(timeout=8)
            print('PASS: um dispositivo/3contextos T1T2T3, decisões idempotentes, fila serial, estabilização, consumos sintéticos isolados e reservatório compartilhado.')
        finally:
            stop.set()
            thread.join(timeout=8)
            for config in configs:
                config['enabled']=False
                api.post('/control/config',json=config).raise_for_status()


if __name__=='__main__':
    main()
