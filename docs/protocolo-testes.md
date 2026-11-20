# Protocolo prático T1/T2/T3

Decisão aprovada em 12/10/2026: **opção A — ensaio simultâneo contínuo de 30 dias**. Orçamento fixo, sem ampliar materiais. Objetivo: comparar economia e desempenho observados nesta bancada. Nenhuma medição física realizada.

## Bancada e alcance estatístico

Adotar três vasos equivalentes, um por tratamento fixo, com um ESP32 e três sensores/bombas/acionamentos. Compartilhar DHT22, reservatório, quatro sensores de carga/um HX711 e INA3221. Manter tratamentos durante todo o ensaio e sortear qual vaso recebe cada um. Não ampliar compras automaticamente.

Este é o mínimo para observar os três tratamentos simultaneamente: **n=1 vaso por tratamento**. Permite estatística descritiva dos casos, séries temporais e avaliação técnica; não separa efeito do tratamento de particularidades da planta/vaso. Dias, ciclos e leituras não são réplicas independentes. Não usar teste entre tratamentos tratando milhares de leituras como tamanho amostral nem afirmar efeitos generalizáveis.

Analisar o total de 30 dias e, complementarmente, dias 1–15 e 16–30. **Não são duas rodadas nem duas réplicas**: manter plantas, substrato, tratamentos, modelo e parâmetros durante todo o período. A passagem pela quinzena não reinicia o controle nem o consumo acumulado.

Base metodológica: [NIST — princípios de delineamento](https://itl.nist.gov/div898/handbook/pmd/section3/pmd33.htm). Não há aprovação estatística inferencial presumida.

## Condições e parâmetros

- Vasos do mesmo modelo (~3 L), substrato do mesmo lote e mesma massa seca inicial; mudas de alface semelhantes, mesma data e preparo. Registrar diferenças iniciais.
- Bancada coberta, mesmo regime de luz, suportes e drenagem individuais. Sortear posições iniciais e usar rodízio de posições previamente registrado, sem mudar tratamento nem forçar fios/mangueiras.
- Sensor em posição/profundidade equivalente, calibrado individualmente; calibrar pesagem, água entregue e energia de cada ramo. Condição inicial definida por pesagem/solo após drenagem estabilizada.
- Aquisição proposta: leitura por vaso a cada 60 s; pesagem/corrente mais frequentes durante ciclos, frequência final definida por ensaio de ruído e resposta. DHT22 comum, sem atribuir três medições ambientais independentes.
- Piloto anterior ao ensaio: medir secagem e resposta a doses pequenas, drenagem, vazão e tempo de estabilização. Definir faixa operacional do índice relativo e histerese maior que a oscilação de medição. Não chamar esse índice de umidade volumétrica.
- T1: escolher dose e intervalo com base na demanda do piloto, congelar ambos; referência competente, sem excesso proposital para favorecer T2/T3.
- T2: dose calibrada, limite inferior e rearme superior; verificar que a lógica rearmará no piloto. T3: mesma faixa/dose/proteções, antecipação baseada na previsão de 30 min (ver especificação abaixo). Modelo prevê solo observado sob a política coletada, não secagem sem irrigação.
- Mesmos limites de segurança (água mínima, dose máxima, duração máxima e intervalo mínimo) para todos. Valores dependem dos componentes e das medições; não importar limites sintéticos.
- Congelar parâmetros, modelo, critérios de validade e protocolo antes do início. Contingência de T3 identificada separadamente, inclusive tempo operando sem previsão.

### Valores iniciais aprovados pelo usuário em 04/11/2026 (confirmar no piloto)

Reservatório de ~1 L para os três vasos: ~800 mL úteis acima da reserva mínima, reposição prevista diariamente e registrada como intervenção. Nenhum valor abaixo foi medido; vazão da bomba (80–120 L/h de anúncio) não confirmada.

- Dose comum: **50 mL por ciclo** (faixa de ajuste 30–80 mL), aplicada por tempo calibrado no piloto; não interromper a bomba por leitura de peso em tempo real.
- **Pesagem por diferença (06/11, implementada):** a balança deriva ~20–40 g/°C, então a massa absoluta não é usada. Cada ciclo registra a mediana estabilizada antes de ligar e depois da estabilização; água retirada = antes − depois. Resultado negativo ou acima de 2× a dose é registrado como `withdrawal_not_measured` (ciclo concluído, água nula). Viés residual de deriva de ~1 g/min no intervalo do ciclo.
- **Reserva do reservatório por contabilidade:** água disponível = volume registrado na reposição − retiradas medidas (dose máxima quando não medida). Sem reposição registrada, nenhum ciclo é aceito (`insufficient_water`).
- T1: horários fixos com número de doses = consumo médio diário do piloto ÷ dose (ex.: 150 mL/dia → 07h, 12h, 17h).
- T2: abaixo do limite inferior, uma dose; aguardar o intervalo mínimo e repetir enquanto o índice não atingir o limite superior (parada pelo alvo exigida no pré-projeto). Faixa e histerese definidas no piloto.
- T3: mesma dose, faixa, parada e proteções do T2, antecipadas pela previsão de 30 min (especificação definida em 06/11, abaixo).
- Proteções comuns: intervalo mínimo de 45 min por vaso, máximo de 300 mL/vaso/dia, duração máxima 2× o tempo calibrado e teto de 10 s, reserva mínima de 200 mL nunca dosada (contabilidade do reservatório), uma bomba por vez.
- Aprovação da pesagem (carga de trabalho ~1,2 kg): deriva ≤ 1 g em qualquer janela de 5 min num registro ≥ 2 h; carga estática de ~1,2 kg por 30 min com variação ≤ 2 g; retirada de 50 g repetida 10 vezes com erro ≤ 2 g em ao menos 9 e estabilização em até 10 s; capacidade dos sensores confirmada acima da carga. Usar referência melhor que o celular. Resultado de 05/11: deriva reprovada (~20 g/°C; ver [ligações](ligacoes-prototipo.md#registro-noturno-sem-carga-0405112026)); consumo só por diferença antes/depois de cada ciclo, e reserva mínima não pode depender apenas da massa absoluta.

### Especificação do T3 (definida em 06/11/2026, congelar antes do início)

- **Alvo:** índice relativo do solo do vaso T3 (`pot-03`) 30 min à frente (`HORIZON_S=1800`), observado sob a política em uso. Não representa secagem sem irrigação.
- **Atributos:** `soil_index`, `temperature_c`, `humidity_pct`, `hour_sin`, `hour_cos` (leitura atual). Sem meteorologia externa: a bancada é coberta.
- **Dados de treino:** somente físicos, do piloto do próprio vaso T3, com a mesma calibração de solo do ensaio, telemetria a 60 s e T3 operando por histerese (regra do T2) durante o piloto. Mínimo técnico: 60 pares válidos. Alvo: ≥ 48 h de piloto com ciclos de rega. Nenhum dado do período de comparação entra no treino.
- **Seleção:** Ridge, Random Forest, Gradient Boosting e XGBoost (hiperparâmetros fixos em `backend/app/ml.py`), com validação temporal em 3 dobras com expurgo e MAE como critério. O vencedor é refeito nos 80 % iniciais e avaliado nos 20 % finais contra a persistência.
- **Implantação:** o vencedor é implantado e congelado mesmo que não supere a persistência; nesse caso, registrar como limitação, sem trocar o modelo durante o ensaio. A versão (`model_version`) vai em cada comando antecipado.
- **Decisão (determinística):** inicia quando a previsão é ≤ limite inferior. Após o início antecipado, a trava (latch) só é liberada quando o índice **observado** atinge o limite superior, igual ao T2. Valem a mesma dose, intervalo mínimo, teto diário e proteções do T2.
- **Contingência:** sem previsão válida, o T3 não rega (`prediction_unavailable`), a menos que `prediction_fallback_enabled` seja aprovado. Sem o serviço de decisão por mais de 90 s, o firmware usa a histerese local. Ambos os casos ficam registrados com o tempo correspondente.
- **Limitação:** piloto curto, n = 1 por tratamento, modelo de um único vaso. Os resultados não generalizam.

## Reservatório e medidores compartilhados

As leituras dos vasos ocorrerão no mesmo período; ciclos de irrigação serão **sequenciais**. Configurações e históricos separados por dispositivo/experimento/vaso; uma fila global por ESP32 protege a instrumentação comum. Não liberar bomba publicada/iniciada apenas porque o comando expirou: exigir resultado terminal e estabilização.

1. Fila identificada por experimento, vaso, tratamento e UUID; intertravamento global permite uma única bomba ativa, inclusive em contingência. Evitar prioridade fixa que favoreça um tratamento; registrar espera.
2. Confirmar bombas desligadas, estabilizar pesagem e registrar massa inicial. Acionar somente o ramo associado ao vaso.
3. Integrar tensão × corrente no ramo comum das bombas, desligar, estabilizar e registrar massa final. Atribuir retirada/energia somente ao ciclo ativo; leituras comuns do reservatório não são consumo por vaso.
4. Persistir resultado, qualidade, horários, versão de calibração e confirmação antes de liberar o próximo ciclo. Falta de confirmação impede presumir que o ramo desligou.
5. Reposição de água somente com todos os ramos desligados, fora de ciclos; registrar intervenção e nova referência. Reposição, vibração, vazamento, sifonamento ou peso instável invalidam a medição afetada, sem fabricar volume zero.

Pesar retirada não garante água entregue: verificar drenagem, vazamentos e variação de água nas mangueiras. INA3221 mede bombas, não o consumo total do sistema. Dimensionar reservatório pela demanda conjunta e frequência de inspeção; ~1 L pode exigir reposição frequente. Dimensionar célula para toda a massa e fonte para o ramo escolhido, sem pressupor componentes comerciais.

## Resultados e visibilidade

Grafana será a visualização central; não criar segunda interface. API/scripts continuam responsáveis por configuração/provisionamento, sem expor segredos no painel.

Resultado principal: água retirada acumulada por vaso na janela válida comum, acompanhada obrigatoriamente da condição do solo. Menor consumo com solo fora da faixa não será classificado automaticamente como desempenho melhor. Energia, confiabilidade e erro preditivo são resultados complementares.

- Por vaso/tratamento: água e energia acumuladas e diárias, quantidade/duração de ciclos, solo na faixa e fora dela, validade/cobertura de dados, erros/falhas e espera na fila.
- Economia observada: `100 × (1 − consumo_Tx / consumo_T1)` na mesma janela com cobertura comparável; T1=0 gera resultado indefinido. Mostrar consumo bruto e ciclos sem medição válida; não apresentar economia completa quando consumo está ausente.
- T3: MAE/RMSE contra observação válida 30 min depois, pareada por vaso/calibração/modelo; avaliar teste temporal separado do treinamento. Mostrar quantidade de pares válidos e cobertura, sem erro fictício para alvos ausentes.
- Falhas/intervenções: identidade, horário, motivo, responsável, efeito na medição, início/fim e ação tomada. Incluir reposição, manutenção, reboot, contingência, leituras inválidas, perda de dados e ciclo incompleto.
- Manter dados brutos e metadados. Estatística de dias/ciclos descreve a série observada; não é evidência de replicação biológica.

Grafana centraliza linhas de solo por vaso/tratamento, comandos, ciclos, decisões, consumo válido observado, cobertura, contingência, intervenções e rejeições. Seletor total30/primeira15/ultima15 é período de análise, não réplica. Relatório descritivo: `tools/experiment_analysis.py`, usando `config/experiment.json`. Ciclos são alocados pelo horário do resultado terminal; comandos sem resultado são apontados, não estimados como consumo zero. Gráficos e alertas adicionais dependem de evidências físicas.

## Calendário e início

**Decisão do usuário em 04/11/2026:** a entrega permanece em 01/12/2026 e a comparação T1/T2/T3 roda pelos dias disponíveis após validação da bancada e piloto, menos que 30 dias. Duração real, início e fim devem ser registrados; a análise usa a janela comum válida e divide em duas metades (não quinzenas fixas), que continuam não sendo réplicas. Modelo de T3 treinado com piloto curto: registrar essa limitação. Os itens abaixo descrevem o plano original.

- Até 31/10: componentes, montagem, calibração, coleta piloto, modelo e testes físicos aprovados. Software sem drivers físicos não significa bancada concluída.
- Ensaio: **01/11/2026 00:00 a 01/12/2026 00:00**, horário de São Paulo — 30 dias completos. Usar intervalo fechado no início e aberto no fim; definir esses limites explicitamente na análise.
- Durante novembro: backups, inspeção diária, gráficos e análise incremental; congelar a coleta só no fim. Entrega em 01/12 deixa poucas horas para fechamento. Modelo T3 precisa existir antes de novembro; treinamento em novembro não substitui 30 dias de avaliação de um modelo congelado.
- Sem bancada/calibração/modelo prontos, não declarar início dos 30 dias nem substituir falhas por simulação. Replanejar prazo ou alcance se não cumprir critérios.

Manter identidade e tratamento de cada vaso; congelar calibração, parâmetros e modelo antes da coleta. Próximas etapas: notebook, drivers, montagem, calibração e ensaio físico. Aquisição e preparação do notebook serão executadas quando o usuário indicar.


**Estado em 01/11/2026:** bancada em diagnóstico com sensores e LED em uma saída BD135; não há ciclo físico de irrigação validado. INA3221 possui três canais, mas a correspondência canal/ramo ainda precisa de conferência. Não atribuir canais pela ordem dos GPIOs. A janela original de 01/11 a 01/12 deve ser replanejada após preparo; manter 30 dias completos. Leituras do sketch de bancada não são dados do ensaio.
