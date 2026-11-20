"""Testa as portas publicadas pelo Docker no notebook e recria só o serviço cuja porta quebrou.

Depois de suspensão/retomada do Windows (11/11/2026) o Docker Desktop voltou com o encaminhamento
de 8090, 8000 e 8883 aceitando a conexão e fechando sem resposta, enquanto os contêineres seguiam
saudáveis por dentro. Uso: `python tools/repair_ports.py` (ou `--check` para só diagnosticar).
"""
import argparse
import socket
import ssl
import subprocess
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def http_ok(url):
    try:
        with urllib.request.urlopen(url, timeout=8) as response:
            return response.status < 500
    except Exception:  # noqa: BLE001 - any failure means the port is not usable
        return False


def tls_ok(host, port):
    """Handshake TLS completo com o broker; a verificação do nome não importa aqui."""
    try:
        context = ssl.create_default_context(cafile=str(ROOT / '.local/certs/ca.crt'))
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        with socket.create_connection((host, port), timeout=8) as raw, context.wrap_socket(raw) as wrapped:
            return wrapped.version() is not None
    except Exception:  # noqa: BLE001
        return False


CHECKS = [
    ('mosquitto', 'broker MQTT 8883', lambda: tls_ok('127.0.0.1', 8883)),
    ('backend', 'API 8000', lambda: http_ok('http://127.0.0.1:8000/health')),
    ('bench-ui', 'painel 8090', lambda: http_ok('http://127.0.0.1:8090/api/live')),
    ('grafana', 'Grafana 3000', lambda: http_ok('http://127.0.0.1:3000/api/health')),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='só diagnosticar, sem recriar')
    args = parser.parse_args()
    broken = []
    for service, label, check in CHECKS:
        ok = check()
        print(f'{label}: {"ok" if ok else "SEM RESPOSTA"}')
        if not ok:
            broken.append(service)
    if not broken or args.check:
        return 1 if broken else 0
    print('Recriando:', ', '.join(broken))
    subprocess.run(['docker', 'compose', 'up', '-d', '--no-deps', '--force-recreate', *broken], cwd=ROOT, check=True)
    if 'mosquitto' in broken:
        # Clients inside Docker reconnect on their own; restart to drop any stale session quickly.
        subprocess.run(['docker', 'compose', 'restart', 'ingestion', 'dispatcher'], cwd=ROOT, check=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
