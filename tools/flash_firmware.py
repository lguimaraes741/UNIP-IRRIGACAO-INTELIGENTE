"""Grava o firmware ESP-IDF e o provisionamento na placa da bancada (migração do sketch Arduino).

Pré-requisitos: backup completo verificado em .local/backups, build em firmware/build e
imagem de provisionamento gerada por tools/provision-device.py. Apaga somente as regiões
que mudam de função na tabela nova (NVS/PHY e journal/events/provision); nunca usa erase-flash.
Usa o esptool local (.local/bench-tools/python no PYTHONPATH).
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / 'firmware/build'
EXPECTED_MAC = '88:57:21:70:7e:b4'
# NVS 0x9000-0xF000 + PHY 0xF000-0x10000 overlap the Arduino nvs/otadata; journal/events/provision
# overlap the old app1 region. Both must start erased for nvs_flash_init to accept them.
ERASE = [(0x9000, 0x7000), (0x190000, 0x30000)]


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def esptool(port, *args, capture=False):
    command = [sys.executable, '-m', 'esptool', '--port', port, '--baud', '460800', *args]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        sys.stderr.write(result.stdout[-2000:] + result.stderr[-2000:])
        raise SystemExit(f'esptool falhou: {" ".join(args[:1])}')
    return result.stdout if capture else None


def monitor(port, seconds, log_path):
    import serial
    link = serial.Serial()
    link.port, link.baudrate, link.timeout = port, 115200, 1
    link.dtr = link.rts = False
    link.open()
    end = time.monotonic() + seconds
    with log_path.open('a', encoding='utf-8') as out:
        while time.monotonic() < end:
            line = link.readline().decode('utf-8', 'replace').rstrip()
            if line:
                print(line)
                out.write(line + '\n')
    link.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', default='COM5')
    parser.add_argument('--provision', required=True, type=Path, help='Diretório com settings.bin e manifest.json')
    parser.add_argument('--backup-manifest', required=True, type=Path, help='manifest.json do backup completo verificado')
    parser.add_argument('--monitor', type=int, default=60, help='Segundos de log serial após gravar')
    args = parser.parse_args()

    backup = json.loads(args.backup_manifest.read_text(encoding='utf-8'))
    image = args.backup_manifest.resolve().parent / 'flash-4mb.bin'
    if not image.is_file() or sha256(image) != backup['flash-4mb.bin']['sha256']:
        raise SystemExit('Backup completo ausente ou divergente do manifesto; não gravar.')
    manifest = json.loads((args.provision / 'manifest.json').read_text(encoding='utf-8'))
    settings = args.provision / 'settings.bin'
    if sha256(settings) != manifest['image_sha256'] or manifest['offset'] != 0x1b0000:
        raise SystemExit('Imagem de provisionamento divergente do manifesto ou da partição.')
    flash_args = (BUILD / 'flash_args').read_text().split()
    if flash_args[:6] != ['--flash_mode', 'dio', '--flash_freq', '40m', '--flash_size', '4MB']:
        raise SystemExit('Build não está configurado para flash de 4 MB.')
    pairs = list(zip(flash_args[6::2], flash_args[7::2]))

    info = esptool(args.port, 'read-mac', capture=True)
    if EXPECTED_MAC not in info.lower():
        raise SystemExit('Placa conectada não é a ESP32 da bancada (MAC diferente).')
    for offset, size in ERASE:
        esptool(args.port, '--after', 'no-reset', 'erase-region', hex(offset), hex(size))
    writes = [item for offset, rel in pairs for item in (offset, str(BUILD / rel))]
    esptool(args.port, '--before', 'default-reset', 'write-flash', '--flash-mode', 'dio', '--flash-size', '4MB',
            *writes, '0x1b0000', str(settings))
    record = dict(flashed_at=time.strftime('%Y-%m-%dT%H:%M:%S%z'), port=args.port,
                  firmware_sha256=sha256(BUILD / 'irrigation_mvp.bin'), provision_sha256=manifest['image_sha256'],
                  erased=[[hex(o), hex(s)] for o, s in ERASE], restore_with=str(image.relative_to(ROOT)))
    log_dir = ROOT / '.local/flash-logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime('%Y%m%dT%H%M%S')
    (log_dir / f'flash-{stamp}.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
    print('Gravação concluída com hash verificado pelo esptool. Monitorando serial...')
    if args.monitor > 0:
        monitor(args.port, args.monitor, log_dir / f'serial-{stamp}.log')


if __name__ == '__main__':
    main()
