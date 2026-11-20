"""Desconexão de dispositivo simulado, ciclo local e reenvio pelo MQTT real."""
from datetime import datetime, timezone
import os
import threading

import httpx
from control_integration_check import wait_for
from simulate import run_loop

stop = threading.Event()
started = datetime.now(timezone.utc)
thread = threading.Thread(target=run_loop,kwargs={'seconds':25,'stop_event':stop,'treatment':'T2',
    'fallback_enabled':True,'offline_after_s':2,'offline_for_s':7},daemon=True)
thread.start()
headers = {'Authorization':'Bearer '+os.environ['API_TOKEN']}
with httpx.Client(base_url='http://backend:8000',headers=headers,timeout=10) as api:
    try:
        def finished():
            rows=api.get('/local-cycles/events',params={'device_id':'sim-01'}).json()
            return next((row['payload'] for row in rows if row['payload']['status']=='completed' and
                         datetime.fromisoformat(row['payload']['occurred_at'])>=started),None)
        complete=wait_for(finished,timeout=22)
        events=api.get('/local-cycles/events',params={'device_id':'sim-01'}).json()
        cycle=[r['payload'] for r in reversed(events) if r['payload']['command_id']==complete['command_id']]
        assert [e['status'] for e in cycle]==['accepted','started','completed'],cycle
        assert all(e['origin']=='local' and e['source']=='simulation' for e in cycle)
        assert api.post('/events',json=complete).json()['status']=='duplicate'
        remote=api.get('/commands',params={'device_id':'sim-01'}).json()
        assert all(r['payload']['command_id']!=complete['command_id'] for r in remote)
        print('PASS: desconexão real de MQTT → fallback sintético → buffer → reenvio → ledger local sem comando remoto.')
    finally:
        stop.set()
        thread.join(timeout=8)
        assert not thread.is_alive(), 'Simulador não encerrou'
