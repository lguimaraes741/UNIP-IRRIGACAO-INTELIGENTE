"""Verifica inferência JSON e T3 com fixture simulada, sem desempenho físico."""
from datetime import datetime, timezone
import os
import threading
from uuid import uuid4

import httpx
from control_integration_check import wait_for
from simulate import run_loop

stop = threading.Event()
started = datetime.now(timezone.utc)
thread = threading.Thread(target=run_loop, kwargs={'seconds':30,'stop_event':stop,'treatment':'T3'},daemon=True)
thread.start()
headers = {'Authorization':'Bearer '+os.environ['API_TOKEN']}
cfg = dict(device_id='sim-01',experiment_id='sim-integration',unit_id='sim-pot-01',treatment='T3',
    source='simulation',enabled=True,calibration_id='sim-fixture-v1',soil_low=70,soil_high=90,
    min_reservoir_mass_g=100,target_volume_ml=10,max_duration_s=5,max_telemetry_age_s=10,cooldown_s=1)
with httpx.Client(base_url='http://backend:8000',headers=headers,timeout=10) as api:
    try:
        def fresh():
            rows=api.get('/telemetry',params={'experiment_id':'sim-integration','source':'simulation'}).json()
            return next((r['payload'] for r in rows if r['payload']['treatment']=='T3' and
                datetime.fromisoformat(r['payload']['collected_at'])>=started),None)
        item = wait_for(fresh)
        api.post('/control/config',json=cfg).raise_for_status()
        response = api.post('/ml/predict',json={'device_id':'sim-01','telemetry_message_id':item['message_id']})
        response.raise_for_status()
        prediction = response.json()
        assert prediction['source']=='simulation' and prediction['model_version'].startswith('ml-')
        decision = api.post('/control/evaluate',json={'device_id':'sim-01','unit_id':'sim-pot-01',
            'experiment_id':'sim-integration','request_id':str(uuid4())}).json()
        assert decision['actuate'] and decision['reason']=='prediction_below_low',decision
        cid=decision['command_id']
        wait_for(lambda:any(r['payload']['command_id']==cid and r['status']=='completed' for r in
                            api.get('/commands',params={'device_id':'sim-01','unit_id':'sim-pot-01',
                                'experiment_id':'sim-integration'}).json()))
        print('PASS: modelo JSON versionado → previsão de fixture → decisão T3 → ciclo simulado confirmado.')
    finally:
        stop.set()
        thread.join(timeout=8)
        cfg['enabled']=False
        api.post('/control/config',json=cfg).raise_for_status()
