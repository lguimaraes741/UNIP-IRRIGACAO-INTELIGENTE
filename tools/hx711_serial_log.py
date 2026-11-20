"""Log the hx711_drift serial diagnostic. Bench evidence only, not trial data."""
import argparse
import time
from datetime import datetime, timezone
from pathlib import Path

import serial  # pyserial


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--tare-after', type=float, default=180,
                        help='Segundos ate enviar uma unica TARA; negativo desativa.')
    parser.add_argument('--duration', type=float, default=1080)
    args = parser.parse_args()
    link = serial.Serial()
    link.port, link.baudrate, link.timeout = args.port, 115200, 1
    link.dtr = link.rts = False  # Do not hold EN/IO0 while opening.
    link.open()
    started = time.monotonic()
    tared = args.tare_after < 0
    # Exclusive create prevents replacing earlier evidence.
    with args.output.open('x', encoding='utf-8') as out:
        out.write('# host_utc;linha_serial (trial_eligible=false)\n')
        while time.monotonic() - started < args.duration:
            if not tared and time.monotonic() - started >= args.tare_after:
                link.write(b'TARA\n')
                tared = True
            line = link.readline().decode('ascii', 'replace').strip()
            if line:
                out.write(f'{datetime.now(timezone.utc).isoformat()};{line}\n')
                out.flush()
    link.close()


if __name__ == '__main__':
    main()
