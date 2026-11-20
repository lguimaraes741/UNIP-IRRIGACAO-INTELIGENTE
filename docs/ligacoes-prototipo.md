# Ligações e diagnóstico da bancada

Estado em **01/11/2026**: sensores e um LED com resistor conectado a uma saída de acionamento. **Bombas desconectadas; circuito de potência não validado.** O LED não comprova capacidade de acionar motores. A pinagem vem do sketch do usuário; fotos e leituras complementam a identificação, sem validar continuidade de todas as trilhas.

## Mapa identificado

Placa ESP32 de 30 pinos, família ESP32 clássica/WROOM; revisão exata não identificada. Dxx na serigrafia corresponde ao GPIOxx neste mapa. GPIO não é posição física na fileira.

| Componente/sinal | GPIO | Identificação |
| --- | --- | --- |
| HX711 DOUT/DT | 5 | Configuração do sketch |
| HX711 SCK | 4 | Configuração do sketch |
| Quatro sensores de carga | Sem GPIO direto | Conjunto ligado a E+, E−, A− e A+ do mesmo HX711 |
| INA3221 SDA | 21 | I²C, endereço configurado 0x40 |
| INA3221 SCL | 22 | I²C |
| Comando de saída 1 | 25 | Segundo borne testado: três pulsos no LED confirmados pelo usuário |
| Comando de saída 2 | 26 | Borne original com LED: pulsos HIGH confirmados visualmente; motores não validados |
| Comando de saída 3 | 27 | Último borne testado: três pulsos no LED confirmados pelo usuário |
| Sensor B / Solo 1 | 32 | Associação identificada pelo usuário |
| Sensor A / Solo 2 | 33 | Associação identificada pelo usuário |
| Sensor C / Solo 3 | 34 | Associação identificada pelo usuário |
| DHT DATA | 18 | Driver DHT22 configurado; leituras recebidas |
| LED auxiliar da placa | 2 | Presente no sketch original; não é saída de bomba |

Não atribuir automaticamente T1/T2/T3 pela posição dos componentes. Os três bornes foram associados aos GPIOs por teste com LED; falta associar canais do INA às cargas. Durante o ensaio, fixar a associação sensor/bomba/tratamento e registrar no provisionamento.

## Componentes e pendências

- INA3221 substitui INA219/INA226. Fotos mostram três resistores R100, coerentes com 0,1 Ω usados no sketch; confirmar continuidade do caminho de corrente e calibrar antes de integrar energia por ciclo. Um chip de três canais não comprova que cada saída passa pelo canal esperado.
- Três BD135 NPN substituem a hipótese de MOSFET. Resistores de base, corrente, dissipação e proteção indutiva precisam de conferência. Diodos não foram identificados; não presumir ausência nem proteção existente.
- As três bombas são do mesmo modelo segundo o usuário, sem inscrição. Anúncio encontrado por semelhança cita 3–6 V e ~200 mA; são dados provisórios, não identificação nem corrente máxima/partida comprovada.
- Alimentação atual por USB. Tensões efetivas de VCC e dos sinais ainda não medidas. Fonte externa de 5 V é intenção, sem capacidade confirmada. Não conectar fonte e USB simultaneamente sem conferir o circuito.
- Pesagem real: **quatro sensores de carga de três fios**, tipo balança, ligados a **um HX711** nos terminais E+, E−, A− e A+, conforme relato do usuário. Formam um conjunto de medição, não quatro leituras separadas. A combinação elétrica dos doze fios ainda não foi verificada; não presumir ligação em paralelo nem deduzir função pela cor. Capacidade individual e total desconhecidas; não somar capacidades presumidas nem testar por sobrecarga. Conferir plataforma, distribuição da carga e resposta em cada apoio com massa conhecida e pequena antes da calibração final.
- Índice de solo do sketch usa limites comuns de ar/água: não é calibração individual no substrato nem umidade volumétrica. Peso e energia exibidos ainda não têm exatidão comprovada.

GPIO5 participa da configuração de inicialização do ESP32; validar boot/reset com o HX711 conectado. GPIO34 é somente entrada, adequado ao sinal analógico. Verificar níveis de 3,3 V, pull-ups e alimentação dos módulos antes de modificar ligações. A célula se liga ao HX711, não diretamente ao ESP32; motores se ligam ao circuito de potência, nunca aos GPIOs.

## O que pode ser feito agora

1. Manter motores desconectados, LED com resistor e etiquetas A/B/C nos sensores.
2. Observar solo, ambiente, peso e INA pelo painel do sketch atual. Endereço observado em 04/11: `http://192.168.15.30/`; DHCP pode alterá-lo. O painel temporário não substitui o Grafana do sistema final.
3. Registrar respostas sem tara/calibração por `python tools/bench_monitor.py --ip 192.168.15.30 --samples 3 --interval 2 --output data/raw/diagnostico-led.jsonl`. Usar nome novo em cada coleta; diretório deve existir. O observador consulta somente `/dados` (o sketch limpa a mensagem temporária ao responder), exporta campos permitidos e marca `trial_eligible=false`. A data é de recepção no computador, não horário de aquisição. Falha permanece ausente; flags INA/HX do sketch indicam inicialização, não saúde contínua. NÃO importar esses registros como ensaio nem confirmar calibração com eles.
4. Depois da preservação do programa atual, usar o diagnóstico sequencial abaixo para identificar o borne com LED. Etiquetar o borne com GPIO25, 26 ou 27 conforme a observação. O teste não identifica os outros bornes sem mover a carga ou medir continuidade.
5. Com alimentação desligada, completar continuidade/identificação dos canais e verificar fonte, proteções e célula. Após validação, implementar drivers no ESP-IDF e calibrar antes de ensaio físico.

## Diagnóstico sequencial preparado, ainda não gravado

Arquivo: `firmware/diagnostics/led_channels/led_channels.ino`; mapa em `firmware/core/bench_pins.hpp`. É um sketch Arduino separado, sem rede, sem alteração de EEPROM e sem sensores; o programa atual deixa de fornecer a página web enquanto esse diagnóstico estiver gravado. Não confundir compilação Arduino com o build ESP-IDF do projeto. No Arduino CLI, incluir `firmware/core` em `compiler.cpp.extra_flags` usando `-I` e um caminho curto para evitar o limite do Windows.

**Antes de gravar:** preservar o fonte atual fora do Git se contiver credenciais, registrar o fator de calibração do painel, identificar flash/partições e fazer backup verificado da memória para restauração. Backup de flash pode conter senhas: guardar em `.local/`, nunca versionar. Não executar apagamento de flash. Nenhuma gravação ou backup é realizado automaticamente por esta documentação.

1. Confirmar motores fisicamente desconectados; o diagnóstico só admite LED com resistor. LOW é estado de teste, não polaridade de motor validada.
2. Compilar no ambiente Arduino para a placa confirmada. Ainda requer compilação e verificação física.
3. Após gravação deliberada, abrir serial a 115200 baud com final de linha newline.
4. Enviar `LED_ONLY`: libera testes por 60 s; não liga saída automaticamente.
5. Enviar `1`, `2` ou `3`, separadamente: pulso HIGH no GPIO25, 26 ou 27 por até 1 s no funcionamento normal do loop. As demais saídas ficam LOW. Observar o LED e registrar o GPIO correspondente ao borne. Não há corte independente de hardware contra travamento.
6. Enviar `OFF` para desarmar. Entrada desconhecida/longa desarma; sessão expira e reboot volta desarmado. Comandos recebidos durante pulso encerram o anterior antes de qualquer novo teste.
7. Restaurar o programa preservado para voltar à página de sensores. Não usar esse diagnóstico para motores ou coleta dos 30 dias.

O firmware ESP-IDF final mantém o bloqueio de atuação física até integração e revisão dos drivers. O ensaio aprovado permanece um ESP32, três vasos e 30 dias simultâneos; testes com LED não são ciclos de irrigação. A janela originalmente proposta para novembro não está comprovadamente iniciada e precisa ser replanejada após preparação.

Referências técnicas: [ESP32](https://documentation.espressif.com/esp32_datasheet_en.html), [INA3221](https://www.ti.com/lit/ds/symlink/ina3221.pdf) e [BD135](https://www.st.com/resource/en/datasheet/bd135.pdf). Conferir fabricante/revisão da peça antes de usar qualquer pinagem de encapsulamento.

Quatro sensores de três fios podem ser combinados em ponte para um HX711; a foto isolada não comprova a ligação atual. Referência: [guia SparkFun](https://learn.sparkfun.com/tutorials/load-cell-amplifier-hx711-breakout-hookup-guide). Não é necessário adquirir placa combinadora para documentar ou testar a montagem existente.

## Verificações desta atualização

Em 01/11/2026, três respostas reais de `/dados` foram registradas em `data/raw/diagnostico-led-20261101.jsonl` (arquivo local ignorado pelo Git). São respostas de bancada, com calibração não verificada; o fator informado foi 45,47. Quatro testes do observador HTTP e o teste C++ com adaptador de GPIO/tempo passaram.

Backup completo de 4 MB realizado e comparado com a flash por `esptool verify-flash`: digest correspondente. Chip identificado como ESP32-D0WD-V3, revisão 3.1. Backup e fonte fornecido com credenciais ficam somente em `.local/backups/esp32-com5-20261101/`, ignorados pelo Git. Manifesto contém SHA-256 e partições; não publicar o binário nem o fonte com senhas.

Diagnóstico compilado com Arduino CLI 1.5.1/ESP32 2.0.17 e gravado apenas em app0 (`0x10000`), com hash conferido. O usuário não observou luz no GPIO25 e confirmou o LED piscando no GPIO26. Na primeira posição, isso identificou o borne originalmente ocupado; depois o usuário moveu o mesmo LED com resistor e confirmou três pulsos no GPIO25 no segundo borne e no GPIO27 no último. Os três bornes estão identificados por ordem de teste, sem presumir posição esquerda/direita. Os testes não validam corrente de motor, proteções nem mapeamento dos canais INA. A aplicação original foi restaurada com hash conferido. A página respondeu novamente em `192.168.15.39` e informou fator 45,47. Como no sketch original há tara automática no boot, não esperar preservação do zero anterior.

Para repetir a identificação em outra montagem, desconectar USB e qualquer fonte, etiquetar o borne original como D26 e mover o mesmo LED com seu resistor para outro par de saída, respeitando a polaridade identificada. Não assumir polaridade apenas pela posição dos parafusos. Confirmar a posição antes de energizar se os polos não estiverem identificados. Repetir diagnóstico e registrar a observação, sem conectar motores. Restaurar novamente o programa de sensores ao encerrar.

[Diagrama de diagnóstico](../.archify/workflow-bancada-led-20261101-120000/bancada-led.html): validação de especificação, geração e verificação do artefato passaram. A inspeção automática em navegador falhou por erro do transporte DevTools no Windows; não declarar essa etapa aprovada. Rótulos em português, controles nativos em inglês.

Resultado final dos bornes: **original = D26; segundo testado = D25; último testado = D27**. Etiquetar fisicamente antes de montar os ramos dos vasos; T1/T2/T3 ainda precisam de associação explícita.


## Revisão da pesagem em 04/11/2026

IP confirmado por MAC e página: `http://192.168.15.30/`; DHCP pode alterá-lo. `/dados` informou fator **20,00**, HX inicializado e leituras de peso; o flag não comprova saúde contínua nem calibração. O fator 45,47 nos backups anteriores é histórico: não restaurar dados antigos sobre a calibração atual.

O usuário informou referência de celular de 221 g, cantos 228/220/223/222 g e repetições centrais 223,67/225,08/224,48/225,10/227,73 g. A origem da referência não foi aferida por nós. Relatou deriva sem carga após reposicionar a plataforma: 11,36/14,31/15,37/15,83/19,59 g aos 1/3/5/10/15 min. São observações relatadas, sem aquisição sincronizada; a pesagem não está validada para pequenos volumes. Não corrigir a deriva por taras repetidas ou ajuste do fator.

Revisão do sketch web fornecido e do exemplo serial enviado pelo usuário:

- `getData()` já retorna média filtrada da HX711_ADC. `pesoRaw` do painel não é ADC bruto. A média externa de dez amostras acrescenta atraso, não explica sozinha a deriva por 15 min. Para comparar, usar uma só camada de filtro e não aplicar zona morta ao dado registrado.
- O exemplo serial isola Wi-Fi, DHT, INA e alternância de saídas, sendo uma base melhor para diagnóstico; não é um firmware final e ainda contém bloqueios.
- No código-fonte público consultado, `start(t, ...)` compara `millis()` com `t + 400` como tempo absoluto desde o boot. Se chamado após a espera pelo Wi-Fi, pode pular a fase de estabilização. A versão efetivamente compilada no sketch original não é conhecida. Preferir estabilização própria por diferença de tempos, mantendo `update()` ativo, e tara explícita após estabilização.
- `while (!LoadCell.update())` não tem prazo; no serial, espera por fator e por confirmação de gravação não chama `update()`. Calibração/tara devem ser estados com prazo, cancelamento e aquisição contínua, sem espera infinita por entrada serial nem `parseFloat()` bloqueante.
- Validar massa positiva e finita, fator finito e não nulo (sinal pode depender da orientação), retorno de `EEPROM.begin/commit` e tamanho/versão da configuração. Não reinstalar automaticamente o fator 31,79 se faltar calibração válida. `getNewCalibration()` já aplica o fator na biblioteca consultada: não é falta de `setCalFactor()` no exemplo serial.
- Atualizar saúde por idade da última amostra, timeouts e finitude; durante tara/dados vencidos, publicar valor ausente/estado inválido. Registrar uptime, idade, taxa observada, fator, offset, início/fim de tara e eventual reconexão. Não rotular leitura filtrada como dado bruto.
- Interrupções podem alongar SCK e interferir no HX711; conferir configuração/versão e temporização. Remover Wi-Fi e outras tarefas em teste controlado pode ajudar a isolar a causa, mas não demonstra interferência elétrica nem defeito mecânico por si só.

Próximo ensaio proposto: pesagem isolada por serial, sem rede nem alternância de saídas, fator 20,00 preservado, filtro da biblioteca explícito, sem segunda média/zona morta/autozero; aquisição contínua, tara manual única após estabilização e registro por 15 min sem tocar na montagem. Antes de gravar, fazer novo backup completo: o backup de 01/11 contém fator antigo. Manter motores desconectados. Não trocar programa, fazer tara ou gravar EEPROM automaticamente nesta revisão.

### Resultado do diagnóstico isolado (04/11/2026, à noite)

Novo backup completo em `.local/backups/esp32-com5-20261104-fator20/`: bootloader/tabela, otadata, app0 e restante conferidos; NVS relida e conferida com o chip parado no bootloader (mudou após reboot por Wi-Fi/PHY). Fator gravado: **19,9955** (painel mostra 20,00). Diagnóstico `firmware/diagnostics/hx711_drift` gravado só em app0; app0 do backup restaurado depois, com hash e página conferidos.

Fotos do usuário: quatro sensores sobre suportes plásticos nas extremidades da tampa, placa fina de compensado (~280 g, estimado ao levantá-la) apoiada nos quatro, sem cabos encostando. Ligação aparenta o arranjo padrão em ponte (pares preto/branco entre sensores vizinhos, quatro vermelhos em E+/E−/A−/A+), sem continuidade medida. DHT22 estava na saída de ar quente do notebook (36,6 °C) e foi afastado.

Balança vazia, sem toque, uma série de cada condição (n=1, não há repetição):

| Condição | Duração | Variação | Ruído (dp) |
| --- | --- | --- | --- |
| Sketch web, horas após boot, DHT a 36 °C, antes de mexer na placa | 5 min | +2,8 g (+0,9 g/min) | — |
| Sketch web, após recolocar a placa | 7 min | +16,5 g (+1,5 g/min) | — |
| Diagnóstico serial sem Wi-Fi, após tara única | 15 min | −5,4 g | 1,1 g |
| Sketch web logo após boot (tara automática) | 15 min | +2,9 g | 0,9 g |

Leitura: logo após o boot, os dois programas ficaram dentro de cerca de ±6 g em 15 min; a subida de ~1 g/min e o acúmulo de 11 para 87 g ocorreram com horas de operação, calor do notebook e manuseio. O Wi-Fi não aparece como causa dominante; a causa da deriva maior continua não isolada (temperatura e acomodação mecânica são hipóteses). A alternância das saídas alterou o peso em ~0,3 g. Às 19:19 o INA3221 retornou leitura inválida (−0,01 V; 1638 mA) com o painel ainda "OPERACIONAL": o firmware final deve rejeitar leituras I²C inconsistentes. Canal 3 do INA acompanha o LED (~21 mA quando GPIO25/27 em HIGH). Dados: `data/raw/deriva-vazio-20261104.jsonl`, `hx711-serial-20261104.log`, `deriva-web-pos-boot-20261104.jsonl` (fora do Git, `trial_eligible=false`).

Próximo passo: registro longo (1–2 h ou noturno) com o DHT22 junto ao HX711/sensores e notebook afastado, para relacionar deriva e temperatura; depois, massa conhecida estável por tempo equivalente a um ciclo (fluência sob carga). A pesagem segue não validada.

Fontes: [implementação HX711_ADC](https://github.com/olkal/HX711_ADC/blob/master/src/HX711_ADC.cpp), [API](https://github.com/olkal/HX711_ADC/blob/master/src/HX711_ADC.h), [configuração](https://github.com/olkal/HX711_ADC/blob/master/src/config.h). O código consultado não comprova a versão instalada no firmware original.

### Registro noturno sem carga (04–05/11/2026)

Sketch web original, plataforma vazia, DHT22 ao lado do HX711, leitura a cada 30 s por 8,3 h (`data/raw/deriva-noturna-20261104.jsonl`; `-parte2` é cópia simultânea do mesmo período; fora do Git, `trial_eligible=false`). Peso foi de −13,7 g a −176,1 g (mín. −183,9 g); temperatura do ar caiu de 29–30 °C para 22,5–23,5 °C por volta das 23h–0h (horário local). Correlação peso × temperatura 0,97, inclinação ~20 g/°C; com temperatura estável (22,3–23,7 °C, últimas 4,5 h) ainda houve tendência de −9,5 g/h.

| Variação absoluta entre leituras | mediana | p90 | p95 | ≤ 2 g |
|---|---|---|---|---|
| 60 s (temperatura estável) | 0,95 g | 2,2 g | 2,7 g | 87 % |
| 120 s (temperatura estável) | 1,2 g | 2,9 g | 3,5 g | 77 % |
| 5 min (noite inteira) | 1,8 g | 5,7 g | 8,6 g | 53 % |

Critério de deriva (≤ 1 g em qualquer janela de 5 min) **reprovado**: nenhuma janela cumpriu. Massa absoluta do reservatório não é confiável por horas (bloqueio "< 200 g de água" não pode depender só dela). Para doses de 50 mL, só a diferença antes/depois de um ciclo curto é candidata, ainda marginal; decide o teste de retirada de 50 g ×10. Hipóteses físicas não isoladas: dilatação/gradiente térmico entre as quatro células meia-ponte e a placa, fluência mecânica. Não compensar por temperatura do ar nem por taras sem modelo validado.

Firmware ESP-IDF gravado em 05/11 (`tools/flash_firmware.py`; backup `esp32-com5-20261104-fator20`). Telemetria física chega ao banco a cada 60 s pelo Wi-Fi/MQTT TLS (`esp32-01`), sem bloqueio do firewall. A página `http://192.168.15.30/` do sketch deixou de existir; acompanhar pelo Grafana. `scale_span_g` (amplitude das amostras brutas em ~1 s) foi adicionado para ajustar o limite de `mass_stable` (provisório 5 g; primeiras leituras ~3,5 g).

### Caixa impressa em 3D (06/11/2026)

O usuário trocou a estrutura de madeira por caixa impressa em 3D, mesma disposição dos sensores; a nova tampa mudou o zero (leitura ~−603 g com o offset antigo), não é deriva. Registro sem carga, caixa direto na mesa, firmware ESP-IDF a cada 60 s, 13:22–16:22 UTC (`data/raw/deriva-caixa3d-20261106.csv`, fora do Git): peso de −612 g a −512 g (+37 g/h) com o ar de 24,4 °C a 26,5 °C; correlação 0,95, ~39 g/°C. Nenhuma janela de 5 min ≤ 1 g; variação em 60 s ≤ 2 g em 68 % dos casos. **A deriva térmica permanece**: pesagem por diferença adotada (ver [protocolo](protocolo-testes.md)). Zero e fator precisam ser refeitos com massa conhecida na caixa nova.

Em 05/11 o usuário desligou o ESP32; ao religar, o notebook estava no cabo de rede (IP 192.168.15.37, antes .35 no Wi-Fi) e o ESP32 não alcançava o broker. Certificado reemitido para .37 e .35 e só a partição `provision` regravada. IP .37 reservado no roteador para o MAC Ethernet do notebook (06/11). Telemetria temporariamente a cada 10 s (`telemetry_s`) para a calibração da balança; voltar a 60 s antes do ensaio.

### Testes guiados de pesagem (06/11/2026, painel :8090)

Sessões em `data/raw/sessoes-balanca/` (fora do Git), recalculadas das contagens brutas; leituras a cada 10 s, 2–3 por etapa de 40 s.

- **Pressão (19:11 local):** depois de soltar, a leitura ficou 2,0 a 4,4 g abaixo da base inicial (fator antigo) com temperatura estável. Indica possível atrito ou contato da tampa; inconclusivo com 2 leituras por etapa.
- **Calibração com pote de 600 g (19:25–19:37):** fator **22,19 contagens/g**, zero 8.572.284. Foram 2 cargas válidas: a 3ª perdeu o vazio final por erro do painel. Repetibilidade de 1,2 g; os 5 degraus do pote têm desvio-padrão de 24 contagens (0,2 %). O fator antigo gravado na placa (20,00) lia o pote como ~666 g.
- **Retirada de 50 g.** Segundo o usuário, a 1ª sessão (19:40–19:54, completa) usou saquinho de ~53 g (sacola de ~3 g não descontada); as seguintes, um copo pesado em exatamente 50 g. A tampa não encosta na caixa (conferido pelo usuário).
  - Copo: 16 retiradas em 4 sessões, 1.017,8 ± 24,8 contagens (DP de ~1,2 g por retirada) → **fator incremental 20,36 contagens/g**. Ajustado só na sessão 20:15 (20,19), as outras 8 retiradas ficam com erro ≤ 2 g em 7 (máx. 2,8 g).
  - Saquinho: 1.064 contagens → 52,3 g pelo fator incremental, coerente com ~53 g. A "queda de 7 %" era a troca de objeto, não deriva de sensibilidade.
  - Com 20,36, 14/16 retiradas do copo ficam em ±2 g (87,5 %); o critério de 9/10 não foi formalmente atingido. Cada etapa teve só 2–3 leituras de telemetria (10 s), enquanto o ciclo real usa mediana de amostras do HX711.
- **Não linearidade (~9 %):** 0 → 600 g dá 22,19 contagens/g; +50 g sobre ~550 g dá 20,36. A massa absoluta não serve para retirada; a pesagem por diferença usa o fator incremental.
- **Gravado na placa (06/11, 20:40 local, só partição `provision`):** `scale_cal=scale-20261106-incremental-50g`, fator 20,356, zero 8.569.719 (balança vazia, após religar o USB). A massa absoluta segue sujeita à deriva térmica e à não linearidade.
- **Pendente antes do ensaio:** retirada de 50 g × 10 com o reservatório na carga de trabalho (~1,2 kg), etapas mais longas e fator 20,356. A pesagem por diferença ainda **não está aprovada**.

Painel corrigido: as etapas não são mais puladas quando a marcação demora (testado com 0,9 s de atraso); o fator sugerido é o gravado na placa (deduzido só de leituras da calibração atual); sessões abertas de páginas recarregadas ficam `abandoned`.

### Firmware `physical-telemetry-2` e testes na placa (06–07/11/2026, só LED, bombas ausentes)

- **Entradas do controle:** o firmware passa ao controle o índice do solo calibrado e a mediana da balança. Como solo e limites ainda não estão calibrados, nenhum comando atua. Pilha da tarefa principal aumentada de 3.584 para 8.192 bytes: a 1ª gravação reiniciava em loop por estouro de pilha.
- **Reservatório:** registro de 1.000/200 mL confirmado pela telemetria e **mantido após reset**. Depois do teste, foi registrado 0 mL (reservatório ainda não montado).
- **Broker parado por 78 s:** as 13 leituras do período chegaram depois da reconexão, sem lacuna.
- **Comandos de teste publicados direto no tópico (fora do despachante):** sem calibração, tratamento trocado, vencido e volume acima do limite foram todos rejeitados, com **0 eventos `started`**. Os eventos de comandos desconhecidos ficam em `rejected_messages`.
- **Relógio:** um comando novo era recusado como "futuro" porque o relógio da placa fica ~1 s atrás do servidor. Agora o firmware aceita até 5 s de diferença (`clock_skew_s`).
- **Falta testar com as bombas e calibrações:** ciclo completo, reset no meio da dose (desconto da dose máxima), queda de Wi-Fi durante a dose e leitura inválida durante a dose. Até lá, esses casos têm só os testes de host.

### Quedas de comunicação (07–10/11/2026) e firmware `physical-telemetry-3`

Fontes cruzadas: telemetria no banco, log do Mosquitto e log de energia do Windows (`Kernel-Power`).

| Falha (horário local) | Notebook | Broker | Leitura |
|---|---|---|---|
| 07/11 02:14 → 09/11 07:33 | Desligado ou suspenso: relógio do Windows ajustado ao voltar | Sem conexões | Lado do notebook |
| 09/11 12:46 → 19:39 | Acordado (sem evento de espera) | Placa fechou a sessão às 12:49 e não houve mais nenhuma tentativa até religar | Lado da placa/Wi-Fi |
| 09/11 20:46 → 23:33 | Acordado | Fechou às 20:48; nenhuma tentativa até religar | Lado da placa/Wi-Fi |
| 09/11 23:47 → 10/11 09:15 | **Entrou em espera (S0) às 23:46–23:47** e só acordou de vez às ~09:13 | 672 tentativas TLS abortadas de 03:06 a 09:15 | Notebook em espera (tampa, botão ou ação do usuário) |

A configuração na tomada já é "nunca suspender" e "nunca desligar a tela"; a espera de 23:47 veio de outra ação (tampa ou botão). Corrigido nos firmwares `-3` e `-4` (gravados em 10–11/11):
- **Trava de envio:** a placa só envia uma mensagem depois da confirmação (PUBACK) da anterior. A biblioteca MQTT descartava a mensagem sem avisar (`CONFIG_MQTT_REPORT_DELETED_MESSAGES` desligado), e o envio parava para sempre, mesmo com a conexão ativa. Agora a placa reenvia após 60 s sem confirmação ou após uma desconexão; os ids evitam duplicatas no banco. Ela também trata a fila cheia da biblioteca, e o aviso de descarte foi ligado.
- **Wi-Fi:** economia de energia desligada (`WIFI_PS_NONE`). Com 5 min sem broker, a placa refaz a associação Wi-Fi (novo DHCP/ARP).
- **Último recurso:** com 20 min sem nenhuma entrega e nenhum ciclo ativo, a placa reinicia com as saídas desligadas. A telemetria em RAM é perdida; os eventos ficam na NVS.
- **Diagnóstico na telemetria:** `uptime_s`, `reset_reason`, `heap_free_b`, `heap_min_b`, `heap_largest_b`, `wifi_rssi_dbm`, `mqtt_connects`, `wifi_disconnects`, `wifi_last_reason`, `publish_timeouts`, `wifi_kicks`. A causa exata das quedas de 09/11 durante o dia segue **não confirmada**.

**Causa confirmada em 10/11, na placa:** a fila de envio em RAM tinha 120 mensagens de ~1 KB. Com telemetria a cada 10 s, uma queda de ~6 min no broker **esgotava a memória**. A placa ficava presa: Wi-Fi com "4-way handshake timeout" (motivo 15) e TLS com "Failed to initialize new connection object". Só religar recuperava. É coerente com as quedas de 09/11 durante o dia e com as 672 tentativas TLS abortadas. No `-4`, a fila é limitada a 24 KB e sempre sobram 48 KB livres. O que passa disso é descartado e contado em `outbox_dropped`, que cobre ~80 s com envio a cada 10 s e ~8 min a cada 60 s.

Testes na placa (`-3`/`-4`):
- **Broker parado por 90 s:** 27 leituras sem lacuna.
- **Broker parado por 5,5 min (`-3`):** a placa travou como descrito acima, e foi isso que confirmou a causa.
- **Broker parado por 5,5 min (`-4`):** recuperou sozinha com 1 reassociação Wi-Fi. A memória mínima foi de 56 KB e 81 mensagens foram descartadas (contadas).

### Pulso manual das bombas (`physical-telemetry-4`, 11/11/2026)

- **Bombas:** JT100 informadas pelo usuário (3–5 V DC, 1–1,5 L/min, 1 m). Corrente de trabalho de ~120–350 mA; 1,5 A é o pico de partida (anúncio, não medido).
- **Foto de 11/11 (placa perfurada):** 3 transistores TO-126 (BD135) com resistores de base, borne de 6 vias para as bombas, LED de teste ainda preso ao borne. Os bornes CH1–CH3 do INA3221 aparecem **sem fios**, então a corrente das bombas talvez não passe pelos shunts. Pela foto, não dá para confirmar o diodo de roda-livre, o valor dos resistores, a fonte de 5 V nem o terra comum. A chave continua desligada.
- **Caminho:** o painel envia `irrigation/v1/esp32-01/manual`, e a placa responde em `.../manual_result` com o tempo ligado e o pico de corrente e a tensão mínima nos 3 canais do INA3221 (amostragem de ~100 ms durante o pulso).
- **Limites no firmware:** até 2 s por pulso, 10 s de descanso por saída, até 10 s ligada por saída a cada 10 min, uma saída por vez, desligamento por timer de hardware e proteção extra no laço principal.
- **Chave:** o recurso fica **travado** até existir `pump_test=1` na partição `provision`. A chave só será gravada depois da revisão do circuito de potência; hoje a placa responde `manual_disabled_until_circuit_review` (testado).

**Chave liberada em 11/11/2026 por declaração do usuário** ("o mecanismo está correto"). Diodo, resistores de base, fonte e terra comum **não foram conferidos** por foto. Gravado `pump_test=1` (só partição `provision`). Primeiros pulsos a seco de 0,5 s, uma saída por vez (`data/raw/acionamentos/`):

| Saída (GPIO) | Pico no INA3221 (amostras a ~100 ms) | Canais sem carga | Tensão mínima |
|---|---|---|---|
| 1 (GPIO25) | CH1 0,178 A | 0,001 A | 5,25 V |
| 2 (GPIO26) | CH2 0,382 A | 0,001 A | 5,09 V |
| 3 (GPIO27) | CH3 0,400 A | 0,001 A | 5,10 V |

- **Mapeamento elétrico:** saída n → canal n do INA3221, observado.
- **Pendente:** associar cada bomba ao seu vaso e tratamento (`ina1..3`).
- **Corrente:** fica dentro da faixa de trabalho informada. A saída 1 mediu cerca de metade das outras; investigar a bomba ou o resistor de base.
- **Tempo ligado:** o `on_ms` de 515–559 ms inclui a latência do laço; a saída é desligada pelo timer em 500 ms.
- **Bomba girando em cada saída (observado pelo usuário em 11/11):** saída 1 (GPIO25, CH1) → bomba 3; saída 2 (GPIO26, CH2) → bomba 2; saída 3 (GPIO27, CH3) → bomba 1. Falta associar as bombas aos vasos e tratamentos. A corrente menor da saída 1 corresponde à bomba 3.
- **Vasos (usuário, 11/11):** bomba n → pot-0n (Tn). Resultado: **pot-01/T1 = saída 3 (GPIO27, CH3)**, **pot-02/T2 = saída 2 (GPIO26, CH2)**, **pot-03/T3 = saída 1 (GPIO25, CH1)**. Provisionado como `out1..3=3,2,1` e `ina1..3=3,2,1`. A telemetria passa a levar `pump_output` e a corrente/tensão do canal de cada vaso. O controle automático ainda não usa `out*` (atuação bloqueada).

**Efeitos dos pulsos sobre o resto da placa (10/11, horário local):**
- **DHT22:** a temperatura sumiu nas três vezes em que o usuário acionou as bombas (22:10, 22:20 e 22:26) e só voltou com reinício da placa.
- **Reinício:** às 22:09 houve um reinício por watchdog (`reset_reason=7`) logo após pulsos.
- **USB:** o conversor USB-serial (CH9102, COM5) passou a responder "dispositivo não está funcionando" depois de pulsos, duas vezes.
- **Hipótese principal (não confirmada):** pico indutivo ou ruído de terra no chaveamento das bombas, por diodo de roda-livre ausente ou ineficaz ou por retorno de corrente perto do ESP32.
- **Mitigação em software (`-4` + DHT):** a cada 5 falhas seguidas, o GPIO18 é reinicializado. `dht_faults`, `dht_streak` e `dht_resets` vão na telemetria. Isso não corrige a causa elétrica.

**Teste controlado (11/11):**
- **Foto:** confirma um diodo axial atrás de cada par de bornes. Os resistores de base parecem ser de 1 kΩ (marrom-preto-vermelho, lido na foto). Com isso, a corrente de base fica em ~2,6 mA, e o BD135 provavelmente não satura (corrente da bomba limitada a ~0,1–0,4 A).
- **Pulso único de 0,5 s:** sem efeito no DHT22.
- **Pulsos de 2 s nas três bombas em sequência:** o DHT22 falhou continuamente logo depois, e a reinicialização do GPIO18 não recuperou (`dht_resets` 4).
- **USB:** o conversor USB-serial voltou a falhar ("dispositivo não está funcionando").
- **Leitura:** com diodos presentes, a hipótese passa a ser a **alimentação**. O INA3221 mede ~5,1–5,3 V no barramento das bombas, valor típico do USB, o que sugere bombas alimentadas pelo 5 V da própria placa ESP32/USB. Nesse caso, a partida das bombas derruba o 5 V/3,3 V e afeta DHT22, CH9102 e ESP32. **Confirmado pelo usuário em 11/11:** os testes rodaram só com o USB do notebook, sem a fonte externa, que já está pronta. Repetir o teste controlado com a fonte externa antes de qualquer conclusão sobre ruído. Não usar os botões das bombas só com o USB.

**Mesmo teste só com a fonte externa (11/11):**
- **Bombas:** correntes de 0,38–0,41 A. Barramento mínimo de 5,16 V, então não houve queda relevante de tensão.
- **ESP32:** não reiniciou.
- **DHT22:** **falhou de novo** logo após os pulsos e não voltou com a reinicialização do GPIO18.
- **INA3221:** em uma amostra, leu 0 V de barramento em CH2/CH3 durante o pulso, sinal de ruído no I2C/terra.
- **Conclusão revista:** não é falta de tensão. É **ruído do motor** (escovas e chaveamento) chegando ao DHT22, que trava e só volta quando perde a energia. Todas as recuperações observadas coincidiram com o religamento da placa.
- **Propostas:**
  - alimentar o VCC do DHT22 por um GPIO livre (GPIO23 ou GPIO19), para o firmware desligar e religar o sensor após falhas;
  - se houver componentes: 100 nF cerâmico em paralelo com cada bomba e junto ao DHT22, e pull-up externo de 10 kΩ no dado.

### Parada de 10/11 23:29 a 11/11 ~15:15

- **Notebook:** entrou em espera às 23:29:45 e o ESP32 perdeu a sessão às 23:29:46. Às 00:29 o notebook entrou em suspensão, e só retomou às 12:06.
- **Docker:** depois da retomada, recriou os contêineres com o encaminhamento das portas 8090, 8000 e **8883** aceitando a conexão e fechando sem resposta. Os serviços estavam saudáveis por dentro.
- **Efeito:** o ESP32 não conseguiu reconectar até o broker ser recriado.
- **Ferramenta nova:** `tools/repair_ports.py` testa as portas e recria só o serviço afetado.
- **Ação do usuário:** impedir a espera do notebook (tampa/botão de energia) durante o ensaio.

### DHT22 alimentado pelo GPIO13 (13/11/2026)

- **Mudança:** o usuário passou o VCC do DHT22 do 3V3 para o **GPIO13**.
- **Firmware:**
  - no boot, desliga o sensor por 1,5 s (VCC e dado em nível baixo, sem pull-up) e espera 2,5 s antes de ler;
  - com 3 falhas seguidas, repete esse ciclo de energia (contado em `dht_resets`);
  - não lê o sensor com bomba ligada nem até 2 s depois (`hardware::pumps_active`).
- **Validado na placa (só USB):**
  - leitura normal 13 s após o boot, sem falhas;
  - um pulso de 0,5 s na saída 2 sem falha e sem reinício.
- **Com fonte externa (13/11):** pulsos de 2 s nas 3 bombas (0,39–0,41 A, mínimo de 5,17 V). Houve 3 falhas de leitura, **1 ciclo de energia automático, e a leitura voltou sozinha**, mantendo-se estável nos 90 s seguintes. A recuperação está validada.
- **Watchdog:** dois reinícios `TASK_WDT` (motivo 6) às 20:51, logo após o religamento da placa. A causa provável (não confirmada por backtrace) é `esp_mqtt_client_publish` esperando a reconexão TLS por até 10 s, com o watchdog em 5 s. Watchdog ajustado para 15 s e timeout de rede MQTT para 8 s, gravados em 13/11. Teste com o broker derrubado e religado 3 vezes seguidas: 3 reconexões, nenhum reinício (uptime contínuo de 223 s) e nenhum aviso de watchdog na serial.

**Decisão do usuário (14/11/2026):** durante o ensaio, o notebook fica **no cabo** (192.168.15.37, reservado). Pelo Wi-Fi ele recebe outro IP (ex.: .21), fora do provisionamento e do certificado, e o ESP32 perde o broker. Materiais para iniciar os testes: comprados pelo usuário.


### Vazão e tempo de dose (14/11/2026)

- **Medição do usuário:** copo medidor, pulsos manuais de 2 s na água, repetidos ("valores persistentes"). Bombas 1 e 2: 50 mL em 2 s (~25 mL/s). Bomba 3: ~40 mL em 2 s (~20 mL/s). O teste 4 do painel (balança) **não foi feito**: a pedido do usuário, estes valores substituem o teste.
- **Tempo de dose de 50 mL:** pot-01 = 2,0 s, pot-02 = 2,0 s, pot-03 = 2,5 s (proporcional; validado pelo usuário no copo com pulsos de 2,5 s na água pelo painel, ~50 mL). A partida do motor não foi medida, então 2,5 s pode entregar um pouco mais que 50 mL. A pesagem por diferença de cada ciclo mostra o volume real.
- **Reinícios durante os pulsos (17:19–17:41):** três reinícios (watchdog 7, watchdog de tarefa 6, pane 4) e um travamento que exigiu desligar da tomada. Tensão de ~5,2 V, com leituras do INA3221 incoerentes no mesmo momento. A hipótese de ruído elétrico das bombas no ESP32 **não foi confirmada**: não houve log serial, porque o USB e a fonte não devem ser ligados juntos sem conferir o circuito. Uma queda durante uma dose desliga a saída (boot com saídas baixas) e o ciclo fica sem conclusão no banco.

### Montagem final e retomada (16/11/2026)

- **Mangueiras:** fixadas pelo usuário acima do substrato, sem enterrar, com a ponta acima do nível da água do reservatório e na mesma posição nos três vasos. Testadas pelo usuário: "funcionando perfeitamente".
- **Retomada:** o sistema ficou desligado de 14/11 a 16/11 e foi religado em 16/11 (~21:40 local). Reinícios às 21:54 e 21:55 (watchdog de tarefa) logo após pulsos. O usuário pediu para ignorar o problema da placa por ora.
- **Bomba 1 (pot-01, saída 3, GPIO27, INA CH3) sem resposta no pulso manual:** a placa conclui o pulso e o CH3 mede 0,38–0,43 A (5,13–5,25 V). Logo, há corrente no ramo. Em 14/11 eram 0,32 A a seco. Corrente com motor sem bombear sugere rotor travado (resíduo depois de dias parada), ar preso ou mangueira obstruída. Causa não confirmada.
- **Solo seco (substrato totalmente seco, confirmado pelo usuário):** pot-01 = 2553, pot-03 = 2547,5 (mediana de 12 leituras em 2 min, variação de 7 e 24). pot-02 = 2598 (12 leituras, variação de 25), registrado às 22:02. O sensor do pot-02 mudou de ~2305 para ~2393 e depois para ~2600 a cada religamento da placa (21:55 e 22:00), sem mudança no substrato. O ponto seco do pot-02 fica sob suspeita até se repetir em outro religamento; os outros dois variaram < 40.
- **Bomba 1 substituída (16/11, ~22:30):** a troca de bornes confirmou o defeito na bomba antiga. Na saída 2 ela puxou 0,36 A e não girou, enquanto a saída 3 acionou a bomba 2 normalmente (0,26 A). O usuário colocou uma bomba nova guardada. Pulsos de 1 s na saída 3, reservatório vazio: 0,071 A no CH3, 5,24 V, funcionando segundo o usuário. Corrente baixa coerente com bomba sem carga hidráulica. Vazão medida pelo usuário no copo (16/11): 2 s = 50 mL. O tempo de dose de 2,0 s do pot-01 foi mantido.
- **Solo saturado (16/11, 23:25, ~30 min após regar à mão até escorrer):** pot-01 = 1201,5, pot-02 = 1326, pot-03 = 1454,5 (12 leituras, variação ≤ 13). Calibração `soil-20261116-dry-sat`: índice 0 = seco, 100 = saturado drenado. O pot-01 ainda caía devagar (1191 às 23:25). O firmware passa a aceitar até 10 pontos além dos extremos, arredondando para 0–100. Além disso, a leitura é inválida.
- **Limites provisórios do piloto:** inferior 55, superior 75; dose 50 mL; 45 min por vaso. Escolhidos acima do meio da faixa para não secar a superfície durante a germinação do manjericão (semeado pelo usuário após a calibração). O protocolo pede ajustá-los com os dados do piloto.

### Piloto iniciado (16/11/2026, 20:40 local / 23:40 UTC)

- **Placa:** `physical-irrigation-1` com `irrigation=1` (boot: "Automatic irrigation ENABLED"). Doses de 2,0, 2,0 e 2,5 s; 50 mL; 45 min por vaso; limites 55/75; telemetria a 60 s. Pulsos manuais passam a descontar do reservatório pela vazão calibrada.
- **Servidor:** `PHYSICAL_CONTROL_ENABLED=true` no `.env` e serviço `controller` (perfil `control`) ativo.
- **Configurações** `tcc-2026`, calibração `soil-20261116-dry-sat`, cooldown 2700 s, teto 300 mL/24 h, telemetria válida por 180 s, estabilização 20 s:
  - T1 (pot-01): a cada 8 h, provisório;
  - T2 (pot-02): histerese;
  - T3 (pot-03): histerese de contingência durante o piloto (`prediction_fallback_enabled`), sem modelo.

  A placa recebeu o heartbeat ("Decision service online"). Primeiras decisões: `soil_at_or_above_high` nos três (índice 100, recém-saturados).
- **Reservatório:** 1000 mL, reserva 400 mL. Manjericão (6 sementes por vaso) semeado pelo usuário após a calibração.
- **Pendente:** treinar e congelar o modelo do T3 após ≥ 48 h com ciclos; ajustar T1 e limites pelo consumo do piloto; definir início/fim da comparação no Grafana.
