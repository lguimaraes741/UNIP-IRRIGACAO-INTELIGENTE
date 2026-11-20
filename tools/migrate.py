"""Migração idempotente e conta da aplicação sem superusuário."""
import os
from pathlib import Path
import psycopg
from psycopg import sql

with psycopg.connect(os.environ['ADMIN_DATABASE_URL']) as conn:
    conn.execute('SELECT pg_advisory_xact_lock(41926012)')
    for migration in sorted(Path('/app/migrations').glob('00[3-9]-*.sql')):
        conn.execute(migration.read_text(encoding='utf-8'))
    if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname='irrigation_app'").fetchone():
        conn.execute('CREATE ROLE irrigation_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE')
    conn.execute(sql.SQL('ALTER ROLE irrigation_app PASSWORD {}').format(sql.Literal(os.environ['APP_DATABASE_PASSWORD'])))
    conn.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
    conn.execute('GRANT CONNECT ON DATABASE irrigation TO irrigation_app')
    conn.execute('GRANT USAGE ON SCHEMA public TO irrigation_app')
    conn.execute('GRANT SELECT,INSERT,UPDATE ON ALL TABLES IN SCHEMA public TO irrigation_app')
    conn.execute('GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO irrigation_app')
    conn.execute('ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT,INSERT,UPDATE ON TABLES TO irrigation_app')
    conn.execute('GRANT SELECT ON ALL TABLES IN SCHEMA public TO grafana_reader')
print('Migração concluída; aplicação sem privilégios administrativos.')
