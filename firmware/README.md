# Firmware preparado para três unidades

Projeto ESP-IDF para um ESP32 e três canais. Drivers de **sensores** implementados (`main/hardware.cpp`): HX711 (mediana de 10 amostras, sem autozero/zona morta), INA3221 (identidade conferida a cada leitura; leituras corrompidas descartadas), solo ADC1 GPIO32/33/34 e DHT22. Saídas GPIO25/26/27 ficam LOW no boot; não há driver de bomba. `IRRIGATION_PHYSICAL_ENABLED` falha a compilação propositalmente até validação do circuito de potência.

## Compilar

Na pasta firmware, com ESP-IDF 5.5.1:

```sh
idf.py set-target esp32
idf.py build
g++ -std=c++17 -Wall -Wextra -Werror -Icore tests/safety_test.cpp -o /tmp/safety-test
/tmp/safety-test
# Compila padrão + simulação em diretório temporário separado:
bash tests/build_check.sh
```

`core/controller.hpp` contém proteção portátil, histerese e retomada explícita. O journal persiste IDs **antes** de qualquer acionamento. Seus 128 slots retêm comandos até a expiração (TTL máximo 300 s); slots vencidos só são reaproveitados no checkpoint que também persiste o piso UTC. Regressão do relógio bloqueia comandos. Observações atualizam o piso em RAM; a persistência ocorre em mutações, sem escrita por segundo. IDs legados sem expiração recebem quarentena conservadora de 300 s a partir do primeiro horário confiável; reboot pode prolongá-la. Estado corrompido, versão desconhecida ou falha de gravação bloqueia atuação sem apagar dados.

`main/main.cpp` prepara Wi-Fi/MQTT com CA, autenticação e identidade por dispositivo. Namespace NVS `settings` na partição `provision`: `device`, `experiment`, `unit1`, `unit2`, `unit3`, `broker` (mqtts), `username`, `password`, `ca`, `ssid`, `wifi_password`, `ntp`. `unit1/unit2/unit3` correspondem a T1/T2/T3 fixos e precisam ser distintos; `unit` legado pode substituir apenas `unit1`. Sem as três identidades, o runtime fica desligado. MQTT inicia após sincronização UTC/SNTP. Nenhum segredo é versionado; ACL limita tópicos por dispositivo.

`IRRIGATION_SIMULATION` habilita fixtures sintéticas de três solos e bombas virtuais, com reservatório comum; exige dispositivo/experimento `sim-`. Padrão de provisionamento: `sim-01`, experimento `sim-three-pots`, unidades `sim-pot-01/T1`, `sim-pot-02/T2`, `sim-pot-03/T3`. Publica uma leitura por unidade a cada cinco segundos, com calibração `sim-fixture-{unit_id}`. DHT/reservatório são valores comuns, não três medições independentes. Fixtures recomeçam no boot e não representam ensaio físico. O modo padrão não publica sensores físicos fictícios.

`core/shared_rig.hpp` mantém executor global: uma bomba por vez, pesagem estabilizada antes/depois, intervalo global e término somente após estabilização. Comandos durante outro ciclo/estabilização são rejeitados, sem começar outro canal. Fallback seleciona unidades em rodízio e não se sobrepõe ao remoto; a fila remota justa fica no backend. Eventos preservam unidade/tratamento. Valores **apenas sintéticos**: limite 50 mL/10 s, intervalo 20 s e estabilização 2 s; não importar para a bancada.

**Dose por tempo e pesagem por diferença (06/11/2026):** a bomba desliga somente pelo tempo do comando; a balança nunca interrompe a dose. `Sensors::mass_g` (mediana estabilizada) é lida antes de ligar e após a estabilização; `cycle_withdrawal_ml` aceita 0 ≤ antes − depois ≤ 2× a dose, em qualquer tara. O evento terminal leva `duration_s` e `volume_ml` (`measurements_valid` = água medida; energia ainda null). `SharedRig::set_reservoir(disponível, reserva)` registra a reposição; cada ciclo iniciado desconta a retirada medida ou a dose máxima. Sem reposição, `insufficient_water`. Desde 06/11 (`physical-telemetry-2`): `mass_g` e o índice de solo vêm da leitura física. A reposição chega por `irrigation/v1/<device>/reservoir` (validade de 5 min, uma vez por `refill_id`, bombas paradas). A contabilidade fica no blob `reservoir` da partição `journal`: a dose máxima é descontada e gravada no início do ciclo, e a diferença é devolvida após a medição. Se a gravação falhar, o reservatório volta a desconhecido. A telemetria leva `reservoir_ml`, `reservoir_reserve_ml` e `reservoir_ref`. Comandos com até 5 s de adiantamento são aceitos (diferença de relógio).

Retomada exige conexão, SUBACK da assinatura, UTC válido, buffer esvaziado e ausência de ciclo ativo. Comandos remotos durante essa barreira são rejeitados. Publicação QoS1 só libera o buffer no PUBACK, sem tratar isso como prova de atuação. Eventos conservam UUID/horário original nos reenvios; medições ausentes são null/inválidas. O journal guarda término máximo conservador para preservar intervalo após reboot.

Eventos usam fila NVS durável de 32 posições, com UUID e horário preservados após reboot. Antes de ligar a bomba virtual, `accepted` já está persistido e três posições estão reservadas para accepted/started/terminal; rejeições concorrentes não consomem essa reserva. Erro na persistência interrompe/bloqueia atuação. PUBACK libera a fila persistindo o novo estado; queda de energia entre PUBACK e checkpoint pode reenviar a mesma mensagem, exigindo deduplicação no backend. Snapshots gravam somente o prefixo usado; testes físicos de desgaste/heap/queda de energia permanecem pendentes. Telemetria usa buffer separado **volátil**, limitado a 32 mensagens, com perdas contabilizadas.

`partitions.csv` preserva a NVS legada em `0x9000` e o início do aplicativo em `0x10000`, ampliando-o para 1,5 MiB. Novos offsets: journal `0x190000`, events `0x1a0000`, provision `0x1b0000` (64 KiB cada). A tabela termina em `0x1c0000` e cabe em 2 MiB; conferir capacidade/placa e hash do mapa antes de qualquer gravação. **Este mapa não pode substituir automaticamente um mapa anteriormente gravado:** os offsets de journal/events/provision mudaram. Preservar e migrar dados antes de qualquer alteração, sem erase automático. Gravado na placa da bancada em 05/11/2026 por `tools/flash_firmware.py` (apaga só NVS/PHY e journal/events/provision, após backup completo verificado).

Configuração usa somente `provision`; não apagar journal/events ao reprovisionar. Antes de trocar device/experimento/unidades, drenar e reconciliar eventos pendentes, que conservam a identidade original. Tratamentos/parâmetros/modelos reais devem permanecer congelados durante os 30 dias, conforme protocolo; dias/leituras não são réplicas independentes.

Pendentes: drivers físicos, calibração, integração física das medições e provisionamento no dispositivo. Watchdog ESP-IDF reinicia o dispositivo; corte elétrico seguro durante reset depende do circuito real. UTC/SNTP, NVS, TLS, RAM e entrega MQTT precisam de ensaio no ESP32. PUBACK confirma broker, não persistência no backend. Após queda de energia durante um ciclo pode haver accepted/started sem terminal; a bomba não retoma automaticamente e o backend pode exigir reconciliação manual para desbloquear o dispositivo. Nenhuma água/energia/conclusão é inventada para encerrar esse ciclo. Não houve teste físico.

Os testes portáteis usam bomba/sensores simulados. Não comprovam GPIO, TLS em ESP32, consumo, calibração ou funcionamento físico.


## Diagnóstico isolado com LED

`core/bench_pins.hpp` registra os GPIOs do sketch do usuário e sensores A/B/C. Não define tratamento, borne, polaridade ou circuito de potência validado. `diagnostics/led_channels/led_channels.ino` é um sketch Arduino separado, sem Wi-Fi, credenciais ou escrita de calibração; não faz parte do build ESP-IDF ; foi compilado para ESP32 e usado em diagnóstico com backup verificado. Ele testa GPIO25/26/27 individualmente por até 1 s, após `LED_ONLY`, com sessão de 60 s. Estado LOW não é confirmação de motor desligado: usar exclusivamente com motores fisicamente desconectados. Instruções e preservação do programa atual em [ligações](../docs/ligacoes-prototipo.md).

Verificação local: `g++ -std=c++17 -Wall -Wextra -Werror -Itests/arduino_stub -Icore tests/led_channels_test.cpp -o /tmp/led-test && /tmp/led-test`, na pasta `firmware/`. O adaptador simula serial, tempo e níveis; verifica bloqueio inicial, exclusão entre canais, pulso, expiração, entrada inválida e rollover. Não comprova compilação Arduino/ESP32, temporização física ou circuito elétrico. Quatro sensores de carga compartilham um HX711; não há quatro canais de massa.

Em 01/11/2026, o usuário confirmou LED no borne de GPIO26. Programa original restaurado após teste, página HTTP e fator 45,47 conferidos. Build Arduino CLI requer `firmware/core` no include path (`compiler.cpp.extra_flags=-I<caminho-curto>/firmware/core`). Backup binário e ferramentas ficam em `.local/`, fora do Git. O teste não valida a potência dos motores.

Identificação visual dos três bornes concluída pelo usuário em 01/11/2026: original GPIO26, segundo testado GPIO25, último GPIO27. Testes com LED não identificam automaticamente canais do INA ou tratamentos e não validam motores.

## Diagnóstico isolado da pesagem

`diagnostics/hx711_drift/hx711_drift.ino`: sketch Arduino separado, sem Wi-Fi, DHT, INA ou alternância de saídas (GPIO25/26/27 em LOW). Lê o fator da EEPROM (512 bytes, endereço 0) sem gravar; sem tara no boot, média extra, zona morta ou autozero. Estabiliza 3 s por diferença de tempo com `update()` contínuo; `TARA` pela serial faz uma tara não bloqueante com prazo. Saída CSV a cada 1 s com uptime, estado, idade da amostra, amostras no intervalo, contagens e gramas da média móvel da biblioteca (não ADC bruto), offset e timeout. Biblioteca HX711_ADC 1.2.12 (config padrão, 16 amostras), instalada em `.local/`. Registro: `tools/hx711_serial_log.py --port COM5 --output data/raw/<novo>.log` (uma `TARA` após 180 s). Gravar só em app0 com backup verificado e restaurar o app0 do backup ao terminar.

## Rega automática física (14/11/2026, `physical-irrigation-1`)

Implementada e **travada** até o provisionamento. `irrigation=1` exige `dose1..3` (ms por vaso, do teste de vazão, 300–10000), `dose_ml`, `soil_low`/`soil_high`, `interval_s` (≥ 600), `soil_cal` com `dry1..3`/`wet1..3`, `out1..3` e balança calibrada. Sem isso, o boot registra "Automatic irrigation locked".
- Cada vaso tem limites próprios (`SharedRig` com `std::array<Limits,3>`). Um esp_timer desliga o GPIO exatamente no tempo de dose. O desligamento do controlador vem em segundos inteiros (dose + 1 s) e serve de reserva. A guarda do laço principal força LOW após dose + 300 ms.
- O evento informa `duration_s` = tempo real ligado.
- Há 60 s entre bombas e 15 s de estabilização antes da pesagem. Pulso manual e ciclo se excluem.
- Sem heartbeat do backend, a placa usa a histerese local (`soil_low`/`soil_high`) nos três vasos.

O backend só decide para a origem física com `PHYSICAL_CONTROL_ENABLED=true` (padrão `false` no compose). Sem esse interruptor, não envia heartbeat para a placa física. O teto diário por vaso é `max_daily_volume_ml` da configuração.

## Telemetria física (05/11/2026)

Uma mensagem por vaso a cada `telemetry_s` (padrão 60 s) após UTC/SNTP: ADC bruto do solo, índice apenas se `soil_cal`/`dryN`/`wetN` existirem, DHT22, massa da balança (contagens HX711 + `scale_cal`/`scale_offset`/`scale_factor`, sinal preservado), `scale_span_g` e `mass_stable` (amplitude da janela ≤ 5 g, provisório) e INA3221 só para vasos com `inaN` mapeado. Leituras comuns (balança, DHT) repetem-se em cada vaso e não são medições independentes. Sem calibração: valores null, nunca padrões. Associação vaso↔sensor provisória: unidade 1/2/3 = GPIO32/33/34 (sensores B/A/C). `limits` permanece não calibrado, então nenhum comando aciona saída.

Contingência: modo remoto exige MQTT conectado **e** heartbeat do serviço de controle (a cada 15 s); sem heartbeat por 90 s o firmware usa a histerese local, mesmo com broker ativo. Retomada continua exigindo a barreira de sincronização. Fila de telemetria em RAM: 120 mensagens (~40 min a 3 vasos/min); perdas contabilizadas no log.

Compilação via Docker (`espressif/idf:v5.5.1`), flash de 4 MB configurado. Gravação e migração do mapa Arduino: `tools/flash_firmware.py` (ver [ambiente local](../docs/ambiente-local.md#esp32-na-rede-bancada-física)). Teste portátil novo: `tests/sensor_math_test.cpp`.
