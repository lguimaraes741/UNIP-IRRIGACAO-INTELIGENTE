"""Emite nova CA local e certificado do broker para acesso pela rede (ESP32 no Wi-Fi).

Os certificados anteriores são arquivados em .local; a chave da CA nova fica em
.local/certs/ca.key para reemitir só o certificado do broker se o IP mudar, sem
reprovisionar a CA do ESP32. Clientes precisam receber o novo ca.crt.
"""
import argparse
from datetime import datetime, timedelta, timezone
import ipaddress
from pathlib import Path
import secrets
import shutil

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

ROOT = Path(__file__).resolve().parents[1]
CERTS = ROOT / '.local/certs'


def pem_key(key):
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


def server_certificate(ca, ca_key, ips, names, now):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    alt = [x509.DNSName(n) for n in ['mosquitto', 'localhost', *names]]
    alt += [x509.IPAddress(ipaddress.ip_address(ip)) for ip in ['127.0.0.1', *ips]]
    cert = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'mosquitto')]))
            .issuer_name(ca.subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=180))
            .add_extension(x509.SubjectAlternativeName(alt), critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(ca_key, hashes.SHA256()))
    return cert, key


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ip', action='append', required=True, help='IPv4 do notebook na rede local')
    parser.add_argument('--dns', action='append', default=[], help='Nome adicional do notebook')
    parser.add_argument('--server-only', action='store_true',
                        help='Reemite apenas o certificado do broker com a CA existente (ca.key)')
    args = parser.parse_args()
    for ip in args.ip:
        if not ipaddress.ip_address(ip).is_private:
            raise SystemExit('Use somente IP privado da rede local.')
    now = datetime.now(timezone.utc)
    if CERTS.exists():
        archive = ROOT / f'.local/certs-archive-{now:%Y%m%dT%H%M%SZ}'
        shutil.copytree(CERTS, archive)
    CERTS.mkdir(parents=True, exist_ok=True)
    if args.server_only:
        ca = x509.load_pem_x509_certificate((CERTS / 'ca.crt').read_bytes())
        ca_key = serialization.load_pem_private_key((CERTS / 'ca.key').read_bytes(), None)
    else:
        ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'TCC local CA (rede)')])
        ca = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
              .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
              .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=365))
              .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
              .sign(ca_key, hashes.SHA256()))
        (CERTS / 'ca.crt').write_bytes(ca.public_bytes(serialization.Encoding.PEM))
        (CERTS / 'ca.key').write_bytes(pem_key(ca_key))
    cert, key = server_certificate(ca, ca_key, args.ip, args.dns, now)
    (CERTS / 'server.crt').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (CERTS / 'server.key').write_bytes(pem_key(key))
    env = ROOT / '.env'
    text = env.read_text(encoding='utf-8')
    if 'MQTT_DEVICE_PASSWORD=' not in text:
        env.write_text(text.rstrip('\n') + f'\nMQTT_DEVICE_PASSWORD={secrets.token_hex(24)}\n', encoding='utf-8')
    print('Certificados atualizados em .local/certs (anteriores arquivados). Reinicie a pilha.')


if __name__ == '__main__':
    main()
