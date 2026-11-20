"""Controle T2 por leitura, sem endpoint manual de avaliação, em simulação."""
from datetime import datetime, timezone
import os
import threading
import time
import httpx
from app.controller_service import tick_once
from control_integration_check import wait_for
from simulate import run_loop

stop=threading.Event()
started=datetime.now(timezone.utc)
thread=threading.Thread(target=run_loop,kwargs={'seconds':20,'stop_event':stop,'treatment':'T2'},daemon=True)
thread.start()
cfg=dict(device_id='sim-01',experiment_id='sim-integration',unit_id='sim-pot-01',treatment='T2',source='simulation',
    enabled=True,calibration_id='sim-fixture-v1',soil_low=30,soil_high=60,min_reservoir_mass_g=100,
    target_volume_ml=10,max_duration_s=5,max_telemetry_age_s=10,cooldown_s=1)
with httpx.Client(base_url='http://backend:8000',headers={'Authorization':'Bearer '+os.environ['API_TOKEN']},timeout=10) as api:
    try:
        def fresh():
            rows=api.get('/telemetry',params={'experiment_id':'sim-integration','source':'simulation'}).json()
            return any(r['payload']['treatment']=='T2' and datetime.fromisoformat(r['payload']['collected_at'])>=started for r in rows)
        wait_for(fresh)
        api.post('/control/config',json=cfg).raise_for_status()
        assert tick_once()==1
        assert tick_once()==0  # mesma leitura/configuração não cria segunda decisão
        wait_for(lambda:any(r['status']=='completed' and datetime.fromisoformat(r['payload']['issued_at'])>=started for r in
                            api.get('/commands',params={'device_id':'sim-01','unit_id':'sim-pot-01',
                                'experiment_id':'sim-integration'}).json()))
        print('PASS: leitura → avaliação automática T2 → comando único → ciclo confirmado.')
    finally:
        stop.set();thread.join(timeout=8)
        cfg['enabled']=False
        api.post('/control/config',json=cfg).raise_for_status()
