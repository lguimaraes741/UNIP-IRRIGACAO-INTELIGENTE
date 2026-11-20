"""Gera CSV sintético para teste do pipeline, sem validade agronômica."""
import csv
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
import sys

path = Path(sys.argv[1])
path.parent.mkdir(parents=True, exist_ok=True)
with path.open('w', newline='', encoding='utf-8') as out:
    writer = csv.DictWriter(out, fieldnames=['collected_at','device_id','unit_id','experiment_id','source',
                           'calibration_id','sensor_valid','soil_index','temperature_c','humidity_pct'])
    writer.writeheader()
    start = datetime(2026,9,1,tzinfo=timezone.utc)
    # 10-min spacing (240 rows = 40 h) so the 30-min horizon has exact targets.
    for i in range(240):
        writer.writerow(dict(collected_at=(start+timedelta(minutes=10*i)).isoformat(),
            device_id='sim-01', unit_id='sim-pot-01',experiment_id='sim-ml-fixture',source='simulation',
            calibration_id='sim-fixture-v1',sensor_valid=True,soil_index=50+10*math.sin(i*math.pi/72),
            temperature_c=24,humidity_pct=60))
print('Fixture sintética criada; não usar como resultado físico.')
