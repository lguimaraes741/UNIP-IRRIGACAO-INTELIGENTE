"""Gera credenciais e certificados locais sem substituir configuração existente."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import ipaddress
import secrets

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID

root = Path(__file__).resolve().parents[1]
env = root / '.env'
certs = root / '.local/certs'
if env.exists() or certs.exists():
    raise SystemExit('Configuração já existe; preservada. Não executar bootstrap novamente.')
certs.mkdir(parents=True)
names = ['POSTGRES_PASSWORD', 'APP_DATABASE_PASSWORD', 'GRAFANA_DB_PASSWORD', 'MQTT_BACKEND_PASSWORD',
         'MQTT_SIM_PASSWORD', 'API_TOKEN', 'GRAFANA_ADMIN_PASSWORD']
env.write_text(''.join(f'{name}={secrets.token_hex(24)}\n' for name in names), encoding='utf-8')
now = datetime.now(timezone.utc)
ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'TCC local development CA')])
ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
      .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
      .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=180))
      .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
      .sign(ca_key, hashes.SHA256()))
server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
server = (x509.CertificateBuilder()
          .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'mosquitto')]))
          .issuer_name(ca_name).public_key(server_key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=5))
          .not_valid_after(now + timedelta(days=90))
          .add_extension(x509.SubjectAlternativeName([x509.DNSName('mosquitto'),
              x509.DNSName('localhost'), x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
          .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
          .sign(ca_key, hashes.SHA256()))
for name, certificate in [('ca.crt', ca), ('server.crt', server)]:
    (certs / name).write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
(certs / 'server.key').write_bytes(server_key.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
# Chave da CA não é persistida: emitir nova CA requer migração explícita dos clientes.
print('Credenciais em .env e certificados em .local/certs. Valores não exibidos.')
