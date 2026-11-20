# Materiais para montagem do TCC

Lista aprovada para **um ESP32 e três vasos**, um por T1/T2/T3 durante 30 dias contínuos. Orçamento fixo: não ampliar materiais, comprar reservas ou renovar plantas/substrato na quinzena. Três vasos permitem comparação simultânea exploratória, sem réplicas por tratamento.

As quantidades indicam o que deve estar disponível, não o que obrigatoriamente precisa ser comprado. Reutilizar cabo USB, fonte compatível, recipientes, vasos, bandejas, suportes e ferramentas; emprestar instrumentos quando possível. Os documentos originais não fixam quantidade de vasos; os três foram escolhidos para observar T1/T2/T3 simultaneamente. O [protocolo](protocolo-testes.md) preserva os 30 dias e registra os limites estatísticos.

## Componentes e montagem

| Material | Quantidade | Para que será usado |
| --- | --- | --- |
| ESP32 DevKit com módulo WROOM e conexão USB | 1 | Ler sensores, enviar dados e controlar a bomba com proteções locais. |
| Cabo USB de dados compatível com a placa | 1 | Programar e alimentar o ESP32 pelo notebook. |
| Sensor capacitivo de umidade do solo | 3 | Medir a condição de umidade do substrato após calibração. |
| DHT22 | 1 | Medir temperatura e umidade do ar na bancada. |
| Sensor de carga de três fios, tipo balança, capacidade não identificada | 4, já disponíveis | Compor o conjunto de pesagem do reservatório, ligado a um único HX711. |
| Módulo HX711 | 1 | Converter o sinal conjunto dos quatro sensores de carga para leitura pelo ESP32. |
| Módulo INA3221 | 1 | Medir tensão e corrente do ramo da bomba para calcular energia. |
| Mini bomba submersível DC sem identificação, três do mesmo modelo | 3 | Irrigação futura; referência de anúncio 3–6 V/~200 mA ainda não confirmada. |
| Transistor BD135 NPN já instalado | 3 | Acionamento em avaliação; teste com LED, sem liberação das bombas. |
| Diodo de proteção e resistores do acionamento | 3 conjuntos | Proteger o circuito e manter a bomba desligada quando não houver comando. |
| Fonte externa de 5 V, capacidade a conferir | 1 prevista | Alimentação futura das bombas; bancada atual alimentada por USB, com LED. |
| Fios de potência, bornes e fusível/proteção | 1 conjunto | Conectar e proteger a alimentação e o acionamento. |
| Protoboard/placa de montagem e jumpers de sinal | 1 conjunto | Montar as conexões dos sensores e do controlador. |
| Reservatório com tampa, aproximadamente 1 litro | 1 | Armazenar a água e permitir a medição por pesagem. |
| Plataforma de pesagem, parafusos e suportes | 1 conjunto | Fixar a célula de carga e apoiar o reservatório. |
| Mangueira compatível com a bomba | Cerca de 1–2 m por vaso | Conduzir a água até o vaso. |
| Conexões e fixadores da mangueira | 1 conjunto | Evitar vazamentos e manter o ponto de irrigação estável. |
| Vaso de aproximadamente 3 litros | 3 | Receber o substrato, a planta e o sensor de solo. |
| Bandeja de drenagem | 3 | Coletar a água drenada e proteger a bancada. |
| Substrato comercial para hortaliças, de lote único | Cerca de 12–15 litros, conferir volume útil dos vasos | Preencher o vaso e realizar a calibração inicial. |
| Muda de alface uniforme | 3 | Servir como condição de referência do protótipo com planta. |

BD135 identificados por foto. Fonte, circuito de base, diodo, resistores e fusível precisam de conferência com a corrente real das bombas. A capacidade da célula de carga deve considerar toda a montagem com o reservatório cheio. Alface, volume do vaso e substrato são propostas de bancada ainda sujeitas à definição do experimento.

## Instrumentos e recursos de apoio

| Recurso | Quantidade | Para que será usado |
| --- | --- | --- |
| Multímetro | 1, próprio ou emprestado | Conferir alimentação e medições elétricas. |
| Balança de referência e massas conhecidas | 1 conjunto, próprio ou emprestado | Calibrar e verificar a pesagem do reservatório. |
| Recipiente de coleta e utensílio graduado | 1 conjunto | Conferir água entregue e vazão da bomba. |
| Ferramentas de montagem e, se necessário, solda | 1 conjunto, próprio ou emprestado | Preparar fios, fixar componentes e concluir conexões. |
| Notebook | 1, já disponível | Executar backend, banco, MQTT, Grafana e treinamento dos modelos. |
| Rede Wi-Fi e tomada próximas | Disponíveis na bancada | Conectar o ESP32 ao ambiente local e alimentar o sistema. |

O software será executado localmente com ESP-IDF, Docker Compose, Mosquitto, FastAPI, PostgreSQL, Grafana e bibliotecas Python de dados/ML. A base de integração já foi verificada com dados simulados.

## Compartilhamento e compra econômica

Manter um ESP32, um DHT22, um HX711 com quatro sensores de carga, um INA3221, uma fonte e um reservatório. Acionar **uma bomba por vez**, identificando retirada de água e energia de cada ciclo. Conferir se o conjunto de fios, bornes, fixadores e placa atende aos três ramos.

O reservatório de ~1 L é candidato; dimensionar autonomia conjunta e massa máxima após o piloto. Reaproveitar recipiente maior se necessário, sem trocar a célula antes de conferir toda a massa. Fonte e proteções dependem da corrente real e da arquitetura de acionamento.

Respeitar o limite do orçamento atual; conferir modelos e reaproveitamento antes da aquisição. Não há compra automática pelo agente. A imagem mostra as três unidades conectadas ao mesmo controlador. Análises de 15 dias reutilizam a coleta contínua, sem novos materiais.
