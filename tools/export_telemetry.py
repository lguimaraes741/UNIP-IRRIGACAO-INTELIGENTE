"""Exporta um experimento/origem, sem misturar simulação e medições físicas."""
import argparse
import csv
from pathlib import Path
from app.store import connect

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('experiment_id')
parser.add_argument('output_csv')
parser.add_argument('--source', choices=['physical', 'simulation'], required=True)
parser.add_argument('--unit-id', help='Filtrar uma unidade antes de treinar modelo; não misturar vasos')
args = parser.parse_args()
with connect() as conn:
    rows = conn.execute('SELECT payload FROM telemetry WHERE experiment_id=%s AND source=%s AND (%s::text IS NULL OR unit_id=%s) ORDER BY collected_at',
                        (args.experiment_id, args.source, args.unit_id, args.unit_id)).fetchall()
if not rows:
    raise SystemExit('Nenhum dado encontrado; exportação não criada')
path = Path(args.output_csv)
path.parent.mkdir(parents=True, exist_ok=True)
with path.open('x', newline='', encoding='utf-8') as out:
    columns = list(rows[0]['payload'])
    writer = csv.DictWriter(out, fieldnames=columns)
    writer.writeheader()
    writer.writerows(row['payload'] for row in rows)
print(f'{len(rows)} leituras de origem {args.source} exportadas.')
