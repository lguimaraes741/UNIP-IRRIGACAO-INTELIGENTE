# MVP de irrigação inteligente

[Abrir diagrama interativo de três vasos e 30 dias](../.archify/workflow-ensaio-continuo-20261012-210000/ensaio-continuo.html).

Fluxo em português, controles nativos do Archify em inglês. Diagrama validado e conferido nos temas claro/escuro; descreve também as etapas físicas pendentes.

![Bancada de diagnóstico atual](imagens/bancada-diagnostico-led.png)

Imagem conceitual da etapa atual: sensores, INA3221, BD135 e uma carga LED. Não comprova circuito elétrico, medição calibrada ou operação das bombas. O ensaio final continua sendo de três vasos; a ilustração antiga em `imagens/montagem-conceitual-mvp.png` é histórica e não descreve os componentes atuais.

O MVP é um protótipo de bancada com **um ESP32, três vasos e três bombas**, conectado a uma aplicação local no notebook. Demonstra medir, transmitir, prever, decidir, irrigar e registrar resultados por tratamento.

Opção A aprovada: manter **T1/T2/T3 fixos por 30 dias simultâneos**, sem ampliar orçamento. Compartilhar ambiente, reservatório e medidores, executando uma bomba por vez e aguardando estabilização. Analisar também quinzenas, sem reiniciar o ensaio ou criar réplicas fictícias. Detalhes no [protocolo](protocolo-testes.md).

A entrega inclui montagem, software, calibração, testes e documentação técnica. Não inclui a criação do texto acadêmico.

## Componentes e responsabilidades

| Parte | Responsabilidade |
| --- | --- |
| Sensor de solo e DHT22 | Medir condição de umidade do substrato, temperatura e umidade do ar. |
| ESP32 | Ler sensores, transmitir dados, receber comandos e controlar a bomba com proteções locais. |
| Quatro sensores de carga de três fios e um HX711 | Pesar o reservatório como conjunto para estimar água retirada por ciclo. |
| INA3221 | Medir tensão e corrente do ramo da bomba para calcular energia. |
| BD135 NPN e bomba | Acionamento instalado em avaliação; a capacidade de acionar motores ainda precisa ser validada. |
| MQTT/Mosquitto | Transportar telemetria, comandos e confirmações com TLS e autenticação. |
| Backend Python/FastAPI | Validar e registrar dados, executar previsão e aplicar regras de decisão. |
| PostgreSQL | Armazenar leituras, previsões, decisões, comandos e resultados. |
| Grafana | Mostrar medições, estado do sistema e histórico de irrigação. |

## Fluxo de funcionamento

1. **Medir:** o ESP32 coleta solo e ambiente. Cada leitura identifica dispositivo, vaso, horário, calibração e validade.
2. **Transmitir:** envia os dados ao backend pelo MQTT. O backend valida e persiste sem duplicar mensagens reenviadas.
3. **Prever:** no modo preditivo, um modelo previamente treinado estima o índice de umidade no horizonte de 30 min. A previsão registra versão do modelo e condições utilizadas.
4. **Decidir:** regras determinísticas avaliam previsão, umidade atual, validade dos dados, água disponível e limites de operação. O modelo não liga a bomba diretamente.
5. **Comandar:** quando necessário, o backend envia um comando identificado, com validade e limites de volume/duração.
6. **Validar e irrigar:** o ESP32 confere o comando e suas proteções locais. Informa aceitação ou rejeição e, quando executado, início e conclusão do ciclo.
7. **Medir o resultado:** registra água retirada do reservatório e energia do ramo da bomba. Leituras instáveis ou ausentes são identificadas, não convertidas em consumo zero.
8. **Exibir:** o painel apresenta o histórico, motivo da decisão, medições e eventuais falhas.

O volume retirado é estimado pela diferença de massa estabilizada antes/depois do ciclo, usando a densidade adotada. A energia é calculada pela integração de tensão × corrente ao longo do tempo. Calibração e coleta de referência devem verificar a água efetivamente entregue ao vaso.

## Modos de operação

| Modo | Funcionamento |
| --- | --- |
| T1 — agendamento fixo | Irriga nos horários e volumes previamente definidos. |
| T2 — limiar com histerese | Irriga conforme limites calibrados, evitando alternância frequente de acionamento. |
| T3 — preditivo | Usa previsão de umidade e regras determinísticas para decidir momento e volume. |
| Contingência local | Quando o modo remoto/preditivo fica indisponível, usa histerese local somente com sensores, água e configuração válidos. |

Cada vaso mantém seu tratamento durante todo o ensaio. Um vaso por tratamento fornece n=1; quinzenas, dias e ciclos não são réplicas independentes.

## Proteções e continuidade

- Bomba desligada na inicialização e ao atingir o limite máximo do ciclo.
- Bloqueio por leitura inválida, água insuficiente ou comando fora dos limites.
- Rejeição de comandos vencidos e prevenção de reexecução de duplicatas.
- Watchdog e registro de falhas; confirmação MQTT não equivale a confirmação de irrigação.
- Buffer limitado de telemetria durante desconexão, com reenvio preservando o horário original.
- Retorno sincronizado ao modo remoto, sem executar comandos antigos nem sobrepor ciclos locais.

Pinagem, limites, frequência de amostragem e parâmetros de histerese serão definidos após conferir os componentes e calibrar a bancada.

## Demonstração esperada

- [ ] Leituras físicas válidas chegam ao banco e aparecem no painel.
- [ ] Uma previsão real do modelo participa da decisão em T3.
- [ ] A bomba executa um ciclo autorizado e retorna confirmações.
- [ ] Água e energia do ciclo são registradas com suas referências de calibração.
- [ ] Comandos duplicados, vencidos e inválidos não provocam atuação indevida.
- [ ] Desconexão demonstra contingência e recuperação dentro dos limites documentados.
- [ ] O histórico permite ligar leitura, previsão, decisão, comando e resultado.

Treinamento depende de dados físicos preliminares suficientes. Dados simulados servem aos testes de integração e permanecem identificados; não comprovam desempenho físico ou economia hídrica.

## Limites e estado atual

O índice calibrado do sensor não será chamado de umidade volumétrica sem calibração apropriada. A pesagem mede retirada do reservatório, e a medição elétrica cobre o ramo da bomba. Chuva externa não molha o vaso coberto e deve ser tratada separadamente em simulação.

O protocolo aprovado usa três vasos, um por tratamento: comparação simultânea exploratória durante 30 dias, sem réplicas por tratamento. Inferência sobre efeitos gerais não é sustentada automaticamente. Economia superior a 20%, MAE inferior a três pontos e conformidade de 95% são metas a avaliar, não garantias.

**Estado em 12/10/2026:** configuração/históricos por vaso, fila compartilhada, ciclos sequenciais e atribuição de consumos verificados em simulação; firmware de três canais compilado. Grafana centraliza a visualização. Drivers, montagem, calibração, modelo com dados físicos e medições reais continuam pendentes; atuação física bloqueada. Previsão estima solo observado sob a política coletada, não cenário sem irrigação.

Documentos relacionados: [materiais](materiais-tcc.md), [tarefas](tasks-tcc.md) e [ambiente local](ambiente-local.md).


**Atualização de bancada em 01/11/2026:** LED em uma saída, motores desconectados. Diagnóstico isolado usa Arduino e a página do sketch do usuário; aplicação final continua ESP-IDF/MQTT/Grafana. Ver [ligações](ligacoes-prototipo.md). A janela original de novembro não está comprovadamente iniciada; replanejar após a preparação, mantendo 30 dias.
