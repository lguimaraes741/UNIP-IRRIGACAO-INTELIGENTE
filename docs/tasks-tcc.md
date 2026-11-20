# Etapas da entrega prática

Entrega em **01/12/2026**. Opção A: 30 dias contínuos, um ESP32 e três vasos (um por tratamento, sem réplicas), orçamento fixo. Quinzenas são análises complementares. Texto acadêmico fora do escopo. Itens marcados correspondem ao trabalho já verificado.

## Planejamento e aquisição

- [x] Avaliar referências e registrar a arquitetura aprovada.
- [x] Revisar materiais para um ESP32 e três ramos, sem expansão de orçamento/reservas.
- [ ] Definir orçamento, peças disponíveis e componentes a comprar.
- [ ] Conferir anúncios e dimensionar fonte, acionamento e conexões.
- [ ] Definir local, substrato, planta e responsável pela inspeção diária.
- [x] Definir protocolo operacional econômico e registrar limite estatístico de n=1 por tratamento.
- [x] Adotar comparação descritiva de 30 dias e quinzenas, sem inferência generalizável ou falsas réplicas.
- [ ] Comprar e receber os materiais.

## Ambiente e montagem

- [x] Preparar backend, PostgreSQL, MQTT/TLS e Grafana.
- [x] Verificar ingestão e persistência com dados simulados.
- [x] Preparar ESP-IDF (Docker), identidade `esp32-01`, certificado com IP do notebook e broker na rede local (05/11).
- [x] Reservar IP do notebook no roteador (06/11: .37) e confirmar acesso do ESP32 pelo firewall.
- [x] Implementar backup e verificar restauração em banco temporário isolado.
- [x] Preparar serviço de backup diário enquanto Docker estiver ativo.
- [ ] Configurar backup e cópia adicional no notebook definitivo.
- [x] Definir pinagem e montar circuito elétrico, hidráulico e plataforma de pesagem (montagem final em 16/11).
- [ ] Calibrar solo, pesagem e medição elétrica; registrar método e erro (16/11: solo com pontos seco/saturado; pesagem reprovada por deriva; erro não quantificado).
- [ ] Medir vazão/água entregue e testar vazamento, sifonamento e estabilidade (vazão medida em copo em 14–16/11; demais pendentes).
- [x] Definir faixa de umidade e limites de duração, volume e intervalo (16/11, provisórios: 55–75, 50 mL, 45 min, 300 mL/dia; revisar com o piloto).

## Diagnóstico da bancada com LED

- [x] Registrar GPIOs do sketch, BD135/INA3221 e sensores A/B/C identificados pelo usuário.
- [x] Preparar observador HTTP somente leitura e sketch isolado para pulsos individuais nos LEDs.
- [x] Compilar o diagnóstico Arduino, verificar backup completo de 4 MB e restaurar programa original após o teste.
- [x] Identificar o borne original com LED como GPIO26 por teste e observação do usuário.
- [x] Mapear os três bornes por teste com LED: original D26, segundo D25 e último D27.
- [x] Associar canais do INA3221 aos ramos e registrar sensor/bomba/tratamento.
- [ ] Conferir alimentação, proteção indutiva, circuito de base, corrente das bombas e fonte.
- [ ] Identificar capacidade da célula de carga e validar massa máxima.
- [x] Revisar sketch web e exemplo serial de pesagem; registrar filtros, bloqueios e ressalvas da biblioteca.
- [x] Fazer novo backup com fator 20,00 e executar diagnóstico isolado de deriva (04/11: ±6 g/15 min após boot nos dois programas; causa da deriva longa não isolada).
- [x] Registrar deriva longa com temperatura junto ao HX711 (05/11: ~20 g/°C e −9,5 g/h com temperatura estável; critério de deriva reprovado).
- [x] Repetir deriva na caixa 3D (06/11: ~39 g/°C, deriva térmica permanece) e adotar pesagem por diferença por ciclo no firmware.
- [x] Refazer zero/fator na caixa nova (06/11: fator incremental 20,356 pelo copo de 50 g, gravado na placa; pote 600 g dá 22,19, não linearidade ~9 %).
- [ ] Retirada de 50 g ×10 com o reservatório na carga de trabalho (~1,2 kg) antes de aprovar a pesagem por diferença (06/11: 14/16 em ±2 g a ~550 g).
- [x] Alimentar `mass_g` pela balança, registrar reposição por MQTT e persistir a contabilidade do reservatório (06/11, testado na placa).
- [x] Implementar drivers de sensores no ESP-IDF e painel ao vivo no Grafana (05/11; compilado, gravação pendente).
- [x] Gravar firmware na placa e confirmar telemetria física no banco (05/11, Wi-Fi/MQTT TLS a cada 60 s).
- [x] Replanejar início/fim após preparação (04/11: dias disponíveis até 01/12; piloto iniciado em 16/11 20:40).

## Firmware e controle

- [x] Implementar contratos, controle T1/T2/T3, comandos e confirmação em simulação.
- [x] Verificar ciclo completo, duplicidade, rejeições, desconexão e contingência simulada.
- [x] Preparar núcleo de segurança, MQTT/TLS e compilar firmware ESP-IDF padrão/simulado.
- [x] Verificar retenção por expiração e persistência de eventos com falhas/reboot simulados; compilar firmware atualizado.
- [x] Preparar provisionamento separado do histórico, sem flash automático; verificar gerador NVS e validações.
- [ ] Validar cortes de energia, desgaste NVS e reconciliação de ciclos interrompidos no dispositivo real.
- [x] Preparar partição de aplicativo de 1,5 MiB e compilar núcleo de três canais.
- [ ] Conferir flash real/mapa antes de gravar; não sobrescrever históricos de mapa anterior.
- [x] Registrar decisões, ciclos simulados, previsões e rejeições; atualizar painéis.
- [x] Implementar configuração/telemetria/modelo por vaso e fila global; verificar ciclos sequenciais simulados.
- [ ] Validar medição compartilhada por ciclo e registro de reposições/intervenções.
- [x] Consolidar consultas Grafana por tratamento/período, qualidade, ciclos locais, falhas e intervenções.
- [x] Implementar relatório descritivo com limites de quinzena, deduplicação e ausência sem consumo zero.

Os itens seguintes exigem integração/validação com os componentes físicos:

- [ ] Implementar sensores e telemetria com identificação, calibração e horário (drivers prontos; calibrações pendentes).
- [ ] Implementar comandos, validade, deduplicação e confirmação de execução.
- [ ] Implementar desligamento no boot, watchdog e bloqueios de segurança.
- [ ] Implementar buffer, reenvio, contingência por histerese e retomada sincronizada.
- [ ] Implementar T1 fixo, T2 por histerese e decisão determinística de T3.
- [ ] Completar registros de decisões, ciclos, água, energia, falhas e painéis.
- [ ] Testar ciclo completo, desconexão, reinicialização, leituras inválidas e comandos rejeitados.

## Dados e aprendizado de máquina

- [x] Implementar exportação, alvo (hoje 30 min) e separação temporal com expurgo.
- [x] Implementar comparação de três algoritmos/persistência e modelo JSON versionado.
- [x] Verificar treinamento, inferência e decisão T3 com dados sintéticos separados.

Os itens seguintes dependem de dados físicos e do protocolo:

- [ ] Coletar dados físicos preliminares e registrar intervenções/reposições.
- [ ] Definir atributos, alvo de 30 min (código alinhado em 05/11) e uso de meteorologia aplicável à bancada.
- [ ] Separar treino, validação e teste temporal sem vazamento de dados.
- [ ] Comparar Ridge, Random Forest, Gradient Boosting e XGBoost com persistência; avaliar MAE/RMSE (pipeline pronto).
- [ ] Versionar modelo e integrar inferência, separada do treinamento.
- [ ] Congelar modelo, parâmetros e protocolo antes da comparação.

## Avaliação e entrega

- [ ] Preparar condições equivalentes conforme o delineamento definido.
- [ ] Executar comparação T1/T2/T3 por pelo menos 30 dias, após preparação.
- [ ] Inspecionar diariamente, registrar falhas/fallback e preservar dados.
- [ ] Analisar água, energia da bomba, umidade, erro preditivo e métricas técnicas.
- [ ] Realizar simulações complementares separadas dos ensaios físicos.
- [ ] Usar análise adequada às réplicas e dependência temporal; registrar limitações.
- [ ] Finalizar gráficos, registros, esquema de montagem, calibração e instruções de execução.
- [ ] Organizar código, dados, modelos e configurações sem credenciais.
- [ ] Ensaiar demonstração, revisar critérios de aceite e entregar artefatos técnicos.

## Marcos propostos

- [ ] Coleta piloto, calibração, modelo e bancada prontos até **31/10** (não cumprido; piloto iniciado em 16/11).
- [ ] Comparação de **01/11 00:00 a 01/12 00:00**, com 30 dias completos (substituída em 04/11: dias disponíveis após o piloto até 01/12).
- [ ] Análise incremental em novembro e fechamento/entrega em **01/12**.

Datas condicionadas à aquisição, calibração e dados suficientes. Três vasos fornecem um caso por tratamento; quinzenas e leituras sucessivas não são réplicas. Não abreviar os 30 dias nem declarar início antes da preparação. Replanejar se necessário, mantendo orçamento fixo e limitações explícitas. Metas de desempenho são expectativas a testar.
