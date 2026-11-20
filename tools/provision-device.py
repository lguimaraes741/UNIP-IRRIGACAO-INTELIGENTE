"""Prepare private NVS settings artifacts; never flash or erase device storage."""
import argparse
import csv
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
KEYS = {'device','experiment','unit','broker','username','password','ca','ssid','wifi_password','ntp'}
# Optional physical acquisition settings; absent => firmware publishes null, never a default.
NUMERIC = {'scale_offset','scale_factor','dry1','wet1','dry2','wet2','dry3','wet3','telemetry_s',
           'dose1','dose2','dose3','dose_ml','soil_low','soil_high','interval_s'}
OPTIONAL = NUMERIC | {'scale_cal','soil_cal','ina1','ina2','ina3','out1','out2','out3','pump_test','irrigation'}


def private_path(path):
    path=Path(path).resolve()
    if not path.is_relative_to((ROOT/'.local').resolve()):
        raise ValueError('Arquivos de credenciais devem ficar em .local')
    return path


def validate_ca(pem):
    try:
        ssl.create_default_context(cadata=pem)
    except (ssl.SSLError, ValueError):
        raise ValueError('CA PEM inválida') from None
    from cryptography import x509
    from datetime import datetime, timezone
    try:
        certs=x509.load_pem_x509_certificates(pem.encode())
        if not certs:
            raise ValueError('CA vazia')
        now=datetime.now(timezone.utc)
        for cert in certs:
            if not cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca or not cert.not_valid_before_utc<=now<cert.not_valid_after_utc:
                raise ValueError('CA inválida ou vencida')
        ssl.create_default_context(cadata=pem)
    except (x509.ExtensionNotFound,ssl.SSLError,ValueError):
        raise ValueError('CA PEM inválida ou vencida') from None

def validate(settings, mode):
    unit_keys={'unit1','unit2','unit3'}
    present=unit_keys & settings.keys()
    if present and present != unit_keys:
        raise ValueError('Forneça as três unidades')
    allowed=(KEYS | unit_keys if present else KEYS) | OPTIONAL
    required=KEYS-{'unit'} if present else KEYS
    if not required <= settings.keys() or not settings.keys() <= allowed or any(not isinstance(v,str) for v in settings.values()):
        raise ValueError('Campos settings inválidos')
    identity_keys=['device','experiment'] + (['unit1','unit2','unit3'] if present else ['unit'])
    if present and (len({settings[k] for k in unit_keys})!=3 or ('unit' in settings and settings['unit']!=settings['unit1'])):
        raise ValueError('Unidades duplicadas ou alias incompatível')
    for key in identity_keys:
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,47}',settings[key]):
            raise ValueError('Identidade inválida')
    sim = settings['device'].startswith('sim-') and settings['experiment'].startswith('sim-')
    if mode=='simulation' and not sim:
        raise ValueError('Simulação exige prefixos sim-')
    if mode=='physical' and (settings['device'].startswith('sim-') or settings['experiment'].startswith('sim-')):
        raise ValueError('Modo físico não aceita prefixos sim-')
    uri=urlsplit(settings['broker'])
    if uri.scheme!='mqtts' or not uri.hostname or uri.username or uri.password or uri.path not in ('','/') or uri.query or uri.fragment:
        raise ValueError('Broker exige mqtts sem credenciais no URI')
    try:
        port=uri.port
    except ValueError:
        raise ValueError('Porta inválida') from None
    if port is not None and not 1 <= port <= 65535:
        raise ValueError('Porta inválida')
    if not 1<=len(settings['ssid'].encode())<=31 or not 8<=len(settings['wifi_password'].encode())<=63:
        raise ValueError('Wi-Fi exige SSID até 31 bytes e senha WPA2 de 8–63 bytes')
    if not 1<=len(settings['username'].encode())<=128 or not 1<=len(settings['password'].encode())<=256:
        raise ValueError('Credenciais MQTT fora dos limites')
    if not re.fullmatch(r'[a-zA-Z0-9.-]{1,253}',settings['ntp']):
        raise ValueError('Servidor NTP inválido')
    if len(settings['ca'].encode())>8192 or 'PRIVATE KEY' in settings['ca']:
        raise ValueError('CA inválida')
    validate_ca(settings['ca'])
    for key in NUMERIC & settings.keys():
        try:
            number=float(settings[key])
        except ValueError:
            raise ValueError('Valor numérico inválido') from None
        if number!=number or number in (float('inf'),float('-inf')):
            raise ValueError('Valor numérico inválido')
    # Manual dry pulses (bench page); '1' only after the power circuit is accepted.
    if settings.get('pump_test','0') not in ('0','1'):
        raise ValueError('pump_test deve ser "0" ou "1"')
    if 'telemetry_s' in settings and not 10<=float(settings['telemetry_s'])<=900:
        raise ValueError('telemetry_s deve ficar entre 10 e 900')
    if 'scale_factor' in settings and abs(float(settings['scale_factor']))<1e-6:
        raise ValueError('Fator da balança nulo')
    for key in ('ina1','ina2','ina3'):
        if key in settings and settings[key] not in ('1','2','3'):
            raise ValueError('Canal INA deve ser 1, 2 ou 3')
    # Unit (pot) -> pump output 1..3 (GPIO25/26/27); all three or none, each output once.
    outs=[settings.get(f'out{i}') for i in (1,2,3)]
    if any(outs) and (None in outs or sorted(outs)!=['1','2','3']):
        raise ValueError('out1..out3 devem mapear os três vasos para saídas 1, 2 e 3 distintas')
    # Automatic irrigation (physical): '1' only with every calibration the firmware needs.
    if settings.get('irrigation','0') not in ('0','1'):
        raise ValueError('irrigation deve ser "0" ou "1"')
    for key in ('dose1','dose2','dose3'):
        if key in settings and not 300<=float(settings[key])<=10000:
            raise ValueError('dose1..dose3 (ms) devem ficar entre 300 e 10000')
    if 'dose_ml' in settings and not 0<float(settings['dose_ml'])<=80:
        raise ValueError('dose_ml deve ficar entre 0 e 80')
    if 'interval_s' in settings and float(settings['interval_s'])<600:
        raise ValueError('interval_s deve ser >= 600')
    if ('soil_low' in settings)!=('soil_high' in settings) or ('soil_low' in settings and not 0<=float(settings['soil_low'])<float(settings['soil_high'])<=100):
        raise ValueError('soil_low/soil_high exigem 0 <= low < high <= 100')
    if settings.get('irrigation')=='1':
        needed={'dose1','dose2','dose3','dose_ml','soil_low','soil_high','interval_s','soil_cal','dry1','wet1','dry2','wet2','dry3','wet3','out1','scale_cal','scale_factor','scale_offset'}
        if not needed<=settings.keys():
            raise ValueError('irrigation=1 exige '+', '.join(sorted(needed-settings.keys())))
    for key in ('scale_cal','soil_cal'):
        if key in settings and not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,47}',settings[key]):
            raise ValueError('Identificação de calibração inválida')
    if any('\x00' in value for value in settings.values()):
        raise ValueError('Settings contêm NUL')
    return settings


def partition_metadata():
    table=ROOT/'firmware/partitions.csv'
    rows=list(csv.reader(line for line in table.read_text().splitlines() if line.strip() and not line.lstrip().startswith('#')))
    row=next((list(map(str.strip,row)) for row in rows if row and row[0].strip()=='provision'),None)
    if not row or len(row)<5 or row[1:3]!=['data','nvs']:
        raise ValueError('Partição provision data/nvs não encontrada')
    def number(value):
        return int(value[:-1],0)*(1024 if value[-1:].upper()=='K' else 1048576) if value[-1:].upper() in ('K','M') else int(value,0)
    offset,size=number(row[3]),number(row[4])
    if offset%4096 or size%4096 or size<0x3000:
        raise ValueError('Partição desalinhada')
    for other in rows:
        other=list(map(str.strip,other))
        if other==row:
            continue
        if len(other)<5 or not other[3] or not other[4]:
            raise ValueError('Tabela exige offsets e tamanhos explícitos')
        start,length=number(other[3]),number(other[4])
        if offset < start+length and start < offset+size:
            raise ValueError('Partição provision sobrepõe outra partição')
    firmware=ROOT/'firmware/build/irrigation_mvp.bin'
    return dict(partition='provision',offset=offset,size=size,table_sha256=hashlib.sha256(table.read_bytes()).hexdigest(),firmware_sha256=hashlib.sha256(firmware.read_bytes()).hexdigest() if firmware.exists() else None)

def prepare(settings,output,idf_path=None,csv_only=False,size=None,mode="simulation"):
    validate(settings,mode)
    output=private_path(output)
    partition = partition_metadata()
    if size is None:
        size = partition['size']
    if size != partition['size']:
        raise ValueError('Tamanho divergente da partição provision')
    if size<0x3000 or size%4096:
        raise ValueError('Tamanho NVS deve ser múltiplo de 4096, mínimo 0x3000')
    output.mkdir(parents=True,exist_ok=True)
    csv_path=output/'settings.csv'
    bin_path=output/'settings.bin'
    if csv_path.exists() or bin_path.exists():
        raise ValueError('Artefatos existentes preservados; use novo diretório')
    with csv_path.open('x',newline='',encoding='utf-8') as handle:
        os.chmod(csv_path,0o600)
        writer=csv.writer(handle)
        writer.writerow(['key','type','encoding','value'])
        writer.writerow(['settings','namespace','',''])
        for key in sorted(settings):
            writer.writerow([key,'data','string',settings[key]])
    if not csv_only:
        if not idf_path:
            raise ValueError('ESP-IDF_PATH necessário para gerar binário')
        generator=Path(idf_path)/'components/nvs_flash/nvs_partition_generator/nvs_partition_gen.py'
        if not generator.is_file():
            raise ValueError('Gerador oficial ESP-IDF não encontrado')
        # No shell and no secrets in argv; stdout/stderr are not relayed.
        process=subprocess.run([sys.executable,str(generator),'generate',str(csv_path),str(bin_path),hex(size)],capture_output=True)
        if process.returncode or not bin_path.is_file() or bin_path.stat().st_size!=size:
            raise RuntimeError('Falha do gerador NVS; artefatos privados preservados para diagnóstico local')
        os.chmod(bin_path,0o600)
    manifest=dict(partition, image_sha256=hashlib.sha256(bin_path.read_bytes()).hexdigest() if bin_path.exists() else None, contains_plaintext_secrets=True, flashed=False)
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    return csv_path,bin_path if not csv_only else None


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--settings',required=True,type=Path,help='JSON privado em .local; senhas ausentes serão solicitadas')
    parser.add_argument('--output',required=True,type=Path,help='Novo diretório em .local')
    parser.add_argument('--mode',required=True,choices=['simulation','physical'])
    parser.add_argument('--idf-path',default=os.environ.get('IDF_PATH'))
    parser.add_argument('--csv-only',action='store_true')
    parser.add_argument('--size',type=lambda s:int(s,0),default=None)
    args=parser.parse_args()
    try:
        settings=json.loads(private_path(args.settings).read_text(encoding='utf-8'))
        for key in ('password','wifi_password'):
            if key not in settings:
                settings[key]=getpass.getpass('Senha MQTT: ' if key=='password' else 'Senha Wi-Fi: ')
        if 'ca_file' in settings:
            settings['ca']=private_path(settings.pop('ca_file')).read_text(encoding='utf-8')
        validate(settings,args.mode)
        prepare(settings,args.output,args.idf_path,args.csv_only,args.size,args.mode)
        print('Artefatos privados preparados em .local. Nenhum dispositivo foi gravado.')
        print('Imagem somente para partição provision da tabela conferida; nunca gravar sobre nvs/journal/eventoutbox.')
        print('Confira capacidade da placa e tabela/firmware compatíveis antes de qualquer gravação manual.')
        print('Modo físico: sensores com drivers; atuação permanece bloqueada. Revise ACL/CA do notebook antes de conectar.')
    except (ValueError,RuntimeError,OSError,ImportError,json.JSONDecodeError):
        print('Provisionamento não concluído. Verifique formato, limites, CA e ESP-IDF; valores secretos não são exibidos.',file=sys.stderr)
        return 1
    return 0


if __name__=='__main__':
    raise SystemExit(main())
