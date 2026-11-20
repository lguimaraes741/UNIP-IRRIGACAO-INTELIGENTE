"""Read-only observer of the user's temporary bench sketch. Not trial ingestion."""
import argparse
import ipaddress
import json
import math
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


FIELDS = ('ina', 'hx', 'peso', 'pesoRaw', 'fator', 'temp', 'umid', 'soloRaw',
          'soloPct', 'tensao', 'corrente', 'potencia', 'led25', 'led26', 'led27')


def local_ip(value):
    address = ipaddress.IPv4Address(value)
    networks = ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')
    if not any(address in ipaddress.IPv4Network(n) for n in networks):
        raise ValueError('Use o IPv4 privado da bancada.')
    return str(address)


def reject_constant(value):
    raise ValueError('JSON com numero nao finito: ' + value)


def snapshot(ip, opener=urllib.request.urlopen):
    ip = local_ip(ip)
    result = {'observed_at': datetime.now(timezone.utc).isoformat(),
              'source': 'physical_bench_diagnostic', 'trial_eligible': False,
              'calibration_verified': False, 'host': ip}
    try:
        # Bypass proxy settings for the explicitly requested private address.
        with opener('http://' + ip + '/dados', timeout=4) as response:
            payload = json.loads(response.read(65537), parse_constant=reject_constant)
        if not isinstance(payload, dict):
            raise ValueError('Resposta nao e objeto JSON.')
        result['read_ok'] = True
        result['reported'] = {key: payload.get(key) for key in FIELDS}
        result['reported_initialization_only'] = {
            key: payload.get(key) for key in ('ina', 'hx')}
    except (OSError, ValueError, TypeError) as error:
        result.update(read_ok=False, reported=None, error=type(error).__name__)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ip', required=True, type=local_ip)
    parser.add_argument('--samples', type=int, default=3)
    parser.add_argument('--interval', type=float, default=2)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if not 1 <= args.samples <= 1000 or not math.isfinite(args.interval) or args.interval < 1:
        parser.error('Use 1..1000 amostras e intervalo finito >= 1 s.')
    # Exclusive create prevents accidentally replacing prior evidence.
    output = args.output.open('x', encoding='utf-8') if args.output else None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({})).open
    failed = False
    try:
        for index in range(args.samples):
            record = snapshot(args.ip, opener)
            failed |= not record['read_ok']
            line = json.dumps(record, ensure_ascii=False, allow_nan=False)
            print(line)
            if output:
                output.write(line + '\n')
                output.flush()
            if index + 1 < args.samples:
                time.sleep(args.interval)
    finally:
        if output:
            output.close()
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
