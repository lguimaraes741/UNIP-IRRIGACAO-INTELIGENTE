"""Backup PostgreSQL e prova de restauração em banco temporário isolado."""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import re
import subprocess
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
PREFIX = ['docker', 'compose', 'exec', '-T', 'postgres']


def run(args, **kwargs):
    return subprocess.run(PREFIX + args, cwd=ROOT, check=True, **kwargs)


def sql(database, statement):
    return run(['psql', '-U', 'irrigation', '-d', database, '-v', 'ON_ERROR_STOP=1', '-At'],
               input=statement.encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.decode().strip()


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def backup():
    folder = ROOT / '.local' / 'backups'
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    path = folder / f'irrigation-{stamp}-{uuid4().hex[:8]}.dump'
    try:
        with path.open('xb') as out:
            run(['pg_dump', '-U', 'irrigation', '-d', 'irrigation', '-Fc', '--no-owner', '--no-acl'],
                stdout=out, stderr=subprocess.PIPE)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    manifest = {'format': 'postgres-custom', 'database': 'irrigation', 'created_at': stamp,
                'bytes': path.stat().st_size, 'sha256': digest(path)}
    path.with_suffix('.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return path


def verify(path):
    path = path.resolve(strict=True)
    manifest = json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))
    if manifest['sha256'] != digest(path) or manifest['bytes'] != path.stat().st_size:
        raise ValueError('Backup alterado ou incompleto; restauração não iniciada')
    database = 'restorecheck_' + uuid4().hex
    assert re.fullmatch(r'restorecheck_[a-f0-9]{32}', database)
    sql('postgres', f'CREATE DATABASE "{database}";')
    try:
        with path.open('rb') as stream:
            run(['pg_restore', '-U', 'irrigation', '-d', database, '--no-owner', '--no-acl', '--exit-on-error'],
                stdin=stream, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        tables = sql(database, "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename;").splitlines()
        if 'telemetry' not in tables:
            raise ValueError('Tabela principal ausente na restauração')
        counts = {table: int(sql(database, f'SELECT count(*) FROM "{table}";')) for table in tables
                  if re.fullmatch(r'[a-z_]+', table)}
        result = {'restoration': 'passed', 'tables': counts, 'backup_sha256': manifest['sha256'],
                  'verified_at': datetime.now(timezone.utc).isoformat()}
        path.with_suffix('.verified.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        return result
    finally:
        # Exclusivamente o banco aleatório criado nesta execução; nunca irrigation.
        sql('postgres', f'DROP DATABASE "{database}";')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['backup', 'verify', 'backup-and-verify'])
    parser.add_argument('path', nargs='?')
    args = parser.parse_args()
    if args.operation == 'verify':
        if not args.path:
            parser.error('verify exige caminho do arquivo .dump')
        print(json.dumps(verify(Path(args.path)), indent=2))
    else:
        path = backup()
        print(path)
        if args.operation == 'backup-and-verify':
            print(json.dumps(verify(path), indent=2))
