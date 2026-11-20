# Ambiente local

Software verificado em simulação em 12/10/2026. A rega física foi implementada em 14/11/2026 e continua travada até o provisionamento (`irrigation=1` com doses e calibração do solo) e até `PHYSICAL_CONTROL_ENABLED=true` no `.env`. Ver [firmware](../firmware/README.md#rega-automática-física-14112026-physical-irrigation-1).

## Preparar e executar

Docker Desktop ativo e Python com `cryptography` para configuração inicial:

```powershell
python tools/bootstrap-local.py
docker compose up -d --build
docker compose ps
```

Credenciais em `.env`, certificados em `.local/certs`, dados/modelos locais fora do Git. Configuração inicial preserva arquivos existentes; instalações antigas precisam incluir `APP_DATABASE_PASSWORD` no `.env`. Migrações executam antes da API. Aplicação usa papel restrito; Grafana somente leitura. Dependências e imagens estão fixadas.

| Serviço | Acesso |
| --- | --- |
| API | http://127.0.0.1:8000/docs — Bearer API_TOKEN |
| Grafana | http://127.0.0.1:3000/d/tcc-ingestion — admin/GRAFANA_ADMIN_PASSWORD |
| Painel de bancada | http://127.0.0.1:8090 — leituras ao vivo e testes guiados de pesagem (serviço `bench-ui`) |
| MQTT | porta 8883 na rede local (ESP32) — TLS, autenticação e ACL por dispositivo |
| PostgreSQL | Rede Docker, sem porta publicada |

ESP32 real: ver [ESP32 na rede](#esp32-na-rede-bancada-física). Não desabilitar TLS. Não remover volumes para reiniciar.

## Verificar software

```powershell
docker compose exec -T backend python -m unittest discover -s tests -v
docker compose --profile simulation run --rm simulator python integration_check.py
docker compose --profile simulation run --rm simulator python control_integration_check.py
docker compose --profile simulation run --rm simulator python automatic_integration_check.py
docker compose --profile simulation run --rm simulator python fallback_integration_check.py
docker compose --profile simulation run --rm simulator python three_units_integration_check.py
```

Testes unitários e de banco em esquemas isolados verificam MQTT/TLS, ACL, duplicidade, expiração, rejeições, decisões, ciclos e desconexão/retomada. Testes de banco exigem `TEST_DATABASE_URL` e são ignorados no comando padrão. Simulação usa exclusivamente identidade `sim-01` e origem `simulation`; testes de integração devem executar sequencialmente. A integração de três vasos usa previsões e doses sintéticas, sem medir economia real.

Verificações em 12 e 14/10/2026: 45 testes Python sem banco e 15 testes de persistência aprovados; integrações de três vasos repetidas, controle, contingência e ML aprovadas; consultas Grafana e restauração isolada verificadas. A proteção de fila rejeita alterações de parâmetros/versão do modelo após a decisão. Testes C++ e compilação ESP-IDF padrão/simulação também aprovados. Não houve ensaio físico.

## Simulação e ML

```powershell
docker compose --profile simulation run --rm simulator python tools/ml_fixture.py /data/raw/sim-ml-fixture.csv
docker compose --profile simulation run --rm simulator python -m app.ml /data/raw/sim-ml-fixture.csv /models/active.json --source simulation --allow-simulation
docker compose --profile simulation run --rm simulator python ml_integration_check.py
```

Pipeline compara Ridge, Random Forest, Gradient Boosting, XGBoost e persistência. Horizonte de 30 min (pré-projeto; confirmar no piloto). Separação temporal com expurgo dos alvos; modelo JSON versionado, sem carregar pickle. Dados sintéticos verificam integração, não desempenho agronômico. Treinamento físico exige dados preliminares, calibração consistente e revisão antes de aprovação. Alvo é solo observado sob a política coletada, não previsão contrafactual sem irrigação.

Para demonstração contínua: `docker compose --profile simulation run --rm simulator python simulate.py --loop --seconds 120 --state /state/device.json --treatment T2`. Configurar `/control/config` pela API com limites explícitos **simulados**. Serviço opcional `docker compose --profile control up -d controller` avalia novas leituras; configuração desabilitada por padrão. Desligar após demonstração com `docker compose --profile control stop controller`.

Para três vasos, usar `simulate.py --loop --seconds 120 --three-units --state /state/three-units.json`. Criar três configurações no mesmo dispositivo/experimento, com unidades distintas e T1/T2/T3 fixos. `/control/evaluate` exige `unit_id` e `experiment_id` quando há múltiplos contextos. `stabilization_s` depende do piloto, não de valores sintéticos. Configurar `model_path` do vaso T3 para artefato compatível; exportar treino com `tools/export_telemetry.py ... --unit-id ID`. A fila revalida leitura/previsão antes de publicar e aguarda resultado terminal; falta de confirmação não libera outro ciclo.

Grafana tem seção ao vivo (idade da última mensagem, ADC bruto do solo, balança, DHT22), seletor total/primeira_metade/segunda_metade, linhas por vaso, consumo válido observado, qualidade, ciclos locais, intervenções, rejeições e MAE/RMSE pareados de 30 min. Metades não são réplicas. A janela física vem das variáveis `inicio`/`fim` do painel: ajustar ao iniciar o ensaio. O seletor de tempo também limita as consultas. Fonte simulation serve somente à verificação técnica.

Intervenções: `/interventions` registra início/fim, tipo, contexto, motivo e responsável opcional. Início suspende publicação no dispositivo; alterações durante ciclo invalidam as medições afetadas. Relatório: `docker compose --profile simulation run --rm simulator python tools/experiment_analysis.py --plan /app/config/experiment.json --output /data/processed/resultado.json`. A configuração de referência contém IDs que devem corresponder à bancada. Não sobrescreve arquivo existente; relatório vazio/sem cobertura não inventa economia nem consumo zero.

Contrato em `backend/app/contracts.py`; decisões, previsões, comandos, eventos remotos/locais e rejeições ficam registrados. PUBACK não comprova atuação. Medições inválidas são null, não zero. Separar sempre origem/experimento nas consultas.

## Backup e firmware

`python tools/backup.py backup-and-verify` cria dump, manifesto SHA-256 e testa restauração em banco temporário isolado. `python tools/backup.py verify CAMINHO.dump` verifica arquivo existente. Restauração testada; banco de trabalho preservado.

`docker compose --profile backup up -d backup` agenda dump diário enquanto Docker estiver ativo. Copiar para local adicional e configurar notebook definitivo. Backup no mesmo equipamento não protege contra sua perda.

[Firmware](../firmware/README.md): drivers de sensores implementados; atuação física bloqueada. Journal limitado reutiliza slots após expiração segura; relógio regressivo e falhas de armazenamento bloqueiam atuação. Eventos têm fila persistente separada de 32 registros; telemetria continua volátil. Validar NVS, desgaste e cortes de energia na placa. Um ciclo interrompido pode exigir reconciliação do estado no backend, sem inventar conclusão ou consumo.

## Preparar provisionamento do ESP32

`tools/provision-device.py` gera CSV/bin NVS privados usando o gerador oficial ESP-IDF; não grava dispositivo. Criar `.local/device-settings.json` com `device`, `experiment`, `unit1`, `unit2`, `unit3` (IDs únicos; T1/T2/T3 nessa ordem), `broker` (`mqtts://endereco-do-notebook:8883`), `username`, `ssid`, `ntp` e `ca_file` (caminho do certificado em `.local`). Senhas MQTT/Wi-Fi ausentes são solicitadas sem eco. Modo simulado exige identidades `sim-`; modo físico mantém drivers/atuação bloqueados.

```powershell
python tools/provision-device.py --settings .local/device-settings.json --output .local/provision-01 --mode simulation --csv-only
# Em terminal ESP-IDF ativo, remover --csv-only para gerar settings.bin.
```

CSV/bin contêm segredos em texto claro; no Windows, chmod não configura ACL: manter `.local` privada, fora do Git e de pastas compartilhadas. O script preserva arquivos existentes. Manifesto registra tamanho/offset/hash da tabela e firmware disponível; isso não comprova firmware já instalado.

Antes de mudar identidade ou contexto de um dispositivo já usado, drenar/reconciliar eventos pendentes com a configuração antiga. O script não migra histórico entre identidades. Núcleo de três canais preparado e compilado; drivers e funcionamento físico continuam pendentes.

Imagem destinada **somente à partição `provision`** de `firmware/partitions.csv`. Conferir capacidade real e compatibilidade antes de gravar: offsets novos não migram históricos do mapa anterior. Não apagar/sobrescrever `nvs`, `journal` ou `events`. Não há flash automático, eFuses ou alteração do broker. Revisar endereço, SAN do certificado, CA e ACL; ensaio na placa ainda pendente. Gerador oficial ESP-IDF 5.5.1 verificado com credenciais sintéticas: imagem de 65.536 bytes e sete testes unitários aprovados. [Gerador oficial NVS](https://docs.espressif.com/projects/esp-idf/en/v5.5/esp32/api-reference/storage/nvs_partition_gen.html).

O formato legado com apenas `unit` continua aceito para artefatos antigos; o firmware de três canais exige `unit1/unit2/unit3`. Não fornecer um mapa parcial. A imagem usa offset/tamanho lidos da tabela atual, sem assumir o mapa anterior: `journal` 0x190000, `events` 0x1a0000 e `provision` 0x1b0000, após aplicação de 1,5 MiB. A capacidade de 2 MiB ou maior e a compatibilidade da tabela/firmware precisam ser conferidas na placa antes de gravar.

## ESP32 na rede (bancada física)

O ESP32 publica pelo Wi-Fi no broker do notebook; o notebook fica ligado com Docker ativo durante o ensaio.

1. Certificado com o IP do notebook: `python tools/rotate-broker-cert.py --ip 192.168.15.37 --ip 192.168.15.35 --dns DANILO-PC` (06/11: cabo .37, Wi-Fi .35; broker do ESP32 em .37). Arquiva os certificados anteriores, guarda `ca.key` em `.local/certs` e cria `MQTT_DEVICE_PASSWORD` no `.env`. Se o IP mudar, usar `--server-only` com o novo IP e reprovisionar só o endereço do broker. **Recomendado: reservar o IP do notebook (DHCP) no roteador.**
2. `docker compose up -d --build`; a porta 8883 fica aberta na rede local (demais serviços só em 127.0.0.1). Se o Windows bloquear a conexão do ESP32, o usuário libera a porta TCP 8883 no Firewall do Windows (rede privada).
3. Provisionamento físico em `.local` (Wi-Fi, broker, CA, `esp32-01`, unidades `pot-01..03`) e chaves opcionais de calibração: `scale_cal`, `scale_offset`, `scale_factor`, `soil_cal`, `dry1..3`, `wet1..3`, `ina1..3`, `telemetry_s`. Gerar o binário no contêiner ESP-IDF: `docker run --rm -v <repo>:/src -w /src espressif/idf:v5.5.1 python tools/provision-device.py --settings .local/provision-esp32-01/settings.json --output .local/provision-esp32-01/nvs --mode physical --idf-path /opt/esp/idf`.
4. Compilar: `docker run --rm -v <repo>/firmware:/project -w /project espressif/idf:v5.5.1 idf.py -D SDKCONFIG=/project/build/sdkconfig build`.
5. Gravar (migra do sketch Arduino; exige backup completo verificado): `python tools/flash_firmware.py --provision .local/provision-esp32-01/nvs --backup-manifest .local/backups/esp32-com5-20261104-fator20/manifest.json` com `PYTHONPATH=.local/bench-tools/python`. Apaga só NVS/PHY e journal/events/provision, grava e mostra 60 s de serial. Voltar ao sketch: regravar `flash-4mb.bin` do backup.
6. Conferir no Grafana (origem `physical`, experimento `tcc-2026`) a seção ao vivo. Telemetria a cada 60 s; até ~40 min ficam em RAM durante queda de rede.

## Painel de bancada (testes de pesagem)

`bench-ui` (`backend/app/bench.py`, página em `backend/app/bench_static/`) mostra as últimas leituras físicas do `esp32-01` (atualização a cada 2 s) e guia três testes com contagem regressiva, voz pt-BR e bipes: pressão da tampa, calibração (vazio/carga ×3) e retirada de 50 g ×10. Cada etapa recebe horário do servidor; sessões ficam em `data/raw/sessoes-balanca/` (fora do Git, `trial_eligible=false`). Resultados usam a mediana das contagens HX711 de cada etapa, descartando os primeiros 15 s; a calibração usa o vazio antes e depois de cada carga para cancelar deriva linear. Leitura do banco apenas; nada é gravado na placa (zero/fator seguem pelo provisionamento). Com telemetria a cada 10 s, a estabilização em até 10 s não é avaliável. Grafana continua sendo a visualização do ensaio.

**Reservatório:** o painel mostra a estimativa enviada pela placa (`reservoir_ml` = volume registrado − doses) e avisa quando restam menos de 3 doses de 50 mL acima da reserva. **Registrar reposição** envia `irrigation/v1/esp32-01/reservoir` pelo usuário MQTT `backend` (ACL só para esse tópico do `esp32-01`), válido por 5 min e aceito uma vez por `refill_id`, só com as bombas paradas. A placa grava o valor na partição `journal` e devolve `reservoir_ref` na telemetria, que serve de confirmação. Registros em `data/raw/reposicoes/`. Volume 0 indica reservatório vazio ou ausente.

**Bombas (teste a seco):** botões para as saídas 1–3 (GPIO25/26/27), escolha de 0,5/1/2 s e Parar tudo. O pulso só é aplicado com a chave `pump_test=1` provisionada; os limites estão no firmware. O resultado mostra o pico de corrente e a tensão mínima por canal do INA3221. Registros em `data/raw/acionamentos/`.

**Portas após suspensão do Windows:** se o painel, a API ou o ESP32 pararem enquanto o Grafana funciona, rode `python tools/repair_ports.py` (`--check` só diagnostica). Ele recria apenas o contêiner cuja porta publicada não responde.

**Vazão (teste 4 do painel):** com as bombas submersas e o reservatório sobre a balança, cada bomba recebe 4 pulsos de 5 s no modo `wet`. O firmware limita esse modo a 10 s por pulso e 120 s por saída a cada 10 min. A água de cada pulso é a diferença da mediana da balança antes e depois (etapas de 40 s, primeiros 15 s descartados). O 1º pulso enche a mangueira e fica fora da média. O resultado mostra mL/s, a variação e o tempo para a dose de 50 mL.

**Calibração do solo:** para cada vaso, "Registrar seco" e "Registrar saturado" (após drenar) gravam a mediana do ADC dos últimos 2 min. São exigidas ≥ 6 leituras com variação ≤ 150; o vaso fica pronto com diferença ≥ 200. Arquivo em `data/raw/calibracao-solo/calibracao-solo.json`. A gravação na placa (`soil_cal`, `dry1..3`, `wet1..3`) é feita pelo provisionamento.

