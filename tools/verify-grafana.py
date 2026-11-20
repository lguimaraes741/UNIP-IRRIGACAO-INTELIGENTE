"""Verificação local do dashboard e da consulta PostgreSQL sem exibir credenciais."""
from pathlib import Path
import base64
import json
import time
from http.client import RemoteDisconnected
from urllib.error import URLError
from urllib.request import Request, urlopen

root = Path(__file__).resolve().parents[1]
env = dict(line.split('=', 1) for line in (root / '.env').read_text().splitlines() if '=' in line)
token = base64.b64encode(('admin:' + env['GRAFANA_ADMIN_PASSWORD']).encode()).decode()


def request(path, payload=None):
    headers = {'Authorization': 'Basic ' + token}
    if payload is not None:
        headers['Content-Type'] = 'application/json'
    req = Request('http://127.0.0.1:3000' + path, headers=headers,
                  data=None if payload is None else json.dumps(payload).encode())
    for attempt in range(10):
        try:
            with urlopen(req, timeout=5) as response:
                return json.load(response)
        except (URLError, RemoteDisconnected, ConnectionError):
            if attempt == 9:
                raise
            time.sleep(1)


dashboard = request('/api/dashboards/uid/tcc-ingestion')
assert dashboard['dashboard']['uid'] == 'tcc-ingestion'
result = request('/api/ds/query', {'from': 'now-1h', 'to': 'now', 'queries': [{
    'refId': 'A', 'datasource': {'uid': 'irrigation-postgres', 'type': 'postgres'},
    'format': 'table', 'rawSql': "SELECT count(*) AS messages FROM telemetry WHERE source='simulation'",
    'intervalMs': 1000, 'maxDataPoints': 100}]})
assert not result['results']['A'].get('error'), 'Consulta Grafana falhou'
frames = result['results']['A']['frames']
assert frames and frames[0]['data']['values'][0][0] > 0
for panel in dashboard['dashboard']['panels']:
    for target in panel.get('targets', []):
        query = target['rawSql'].replace('${source:sqlstring}', "'simulation'").replace('${experiment:sqlstring}', "'sim-integration'").replace('${period:sqlstring}', "'total30'")
        target_result = request('/api/ds/query', {'from': str(int(time.time()*1000)-86400000),
            'to': str(int(time.time()*1000)), 'queries': [{'refId':'A','datasource':{'uid':'irrigation-postgres','type':'postgres'},
            'format':target['format'],'rawSql':query,'intervalMs':1000,'maxDataPoints':100}]})
        assert not target_result['results']['A'].get('error'), f"Consulta do painel {panel['id']} falhou"
print('PASS: dashboard provisionado e consultas dos painéis executadas com fonte simulada.')
