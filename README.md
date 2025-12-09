# 🏭 Warehouse Automation System: A Hybrid IoT Architecture for AMR Coordination

**Autoria:** Ivo Rosa, Universidade de Coimbra

Este projeto implementa uma solução completa de **IoT Industrial (IIoT)** para um armazém autónomo simulado. O sistema integra uma frota de **Robôs Móveis Autónomos (AMRs)** e sensores (Prateleiras e Estações) numa arquitetura orientada a eventos.

[cite_start]O foco principal é a gestão de múltiplas fontes de I/O e a garantia de transferência de dados fiável via **MQTT** para telemetria, utilizando **UDP** para sinalização crítica de baixa latência (alertas e *overrides*)[cite: 7, 8, 9].

---

## 🏗️ Arquitetura do Sistema

A arquitetura desacopla a camada operacional "crua" (simulação física) da lógica de gestão de alto nível. [cite_start]Um **Broker MQTT Mosquitto** atua como a espinha dorsal da comunicação, enquanto sockets UDP diretos lidam com sinais críticos[cite: 14, 18].

### Diagrama de Componentes
O sistema é composto pelos seguintes módulos principais:

| Componente | Script Python | Função Principal descrita no Relatório |
| :--- | :--- | :--- |
| **Warehouse Gateway** | `warehouse_gateway.py` | **Ponte Central e Tradutor**. Realiza a normalização de dados (Semantic Adapter) e a tradução de protocolo (JSON $\to$ Binário). [cite_start]Aloja o Servidor de Alertas UDP (Porta 9090)[cite: 28, 43]. |
| **Fleet Coordinator** | `fleet_coordinator.py` | **Cérebro**. Mantém o "World State" em tempo real. Executa a lógica de *Task Allocation* usando um algoritmo heurístico *greedy*. [cite_start]Aloja o Servidor de Encomendas UDP (Porta 9091)[cite: 46, 50]. |
| **System Monitor** | `system_monitor.py` | **Tolerância a Falhas**. [cite_start]Analisa a telemetria para detetar anomalias (Stall, Bateria Crítica) e envia comandos de *override* via UDP[cite: 73, 77]. |
| **AMR Simulator** | `amr_robot.py` | Simula a máquina de estados finitos do robô, decaimento de bateria ($1\%/s$) e falhas estocásticas (5% probabilidade de `STALLED`)[cite: 56, 58]. |
| **Smart Infrastructure** | `shelves.py`, `loading_station.py` | [cite_start]Ativos estáticos que reagem autonomamente (decremento de stock no `PICKING`, auto-reabastecimento após 2s)[cite: 61, 63]. |

---

## 🛠️ Especificações Técnicas e Protocolos

### 1. Protocolo de Controlo Binário (Gateway $\to$ AMR)
Para otimizar a largura de banda e garantir processamento determinístico, os comandos de controlo não usam JSON. [cite_start]O Gateway serializa os comandos num **payload binário fixo de 3 bytes** usando a biblioteca `struct` (`format 'BBB'`)[cite: 30, 33].

| Byte | Função | Exemplo / Descrição |
| :--- | :--- | :--- |
| **Byte 0** | `Command Type` | [cite_start]Identifica a ação (e.g., `0x01` executa tarefa, `0x03` força carga)[cite: 35]. |
| **Byte 1** | `Target Shelf` | [cite_start]ID numérico da prateleira de destino[cite: 36]. |
| **Byte 2** | `Target Station` | [cite_start]ID numérico da estação de embalamento[cite: 37]. |

### 2. Estrutura de Tópicos MQTT (REST-like)
[cite_start]A hierarquia reforça a separação de responsabilidades[cite: 23]:
* **Telemetria:** `warehouse/{GroupID}/{Resource}/{ID}/{Attribute}` (ex: `.../amr/RBT1/status`)
* [cite_start]**Wildcards:** O Monitor e o Debugger subscrevem `warehouse/{GroupID}/#` para ingerir o fluxo completo de dados[cite: 24].

### 3. Normalização de Dados (Data Pipeline)
[cite_start]O Gateway atua como um adaptador semântico para padronizar métricas de diferentes zonas do armazém antes da ingestão no InfluxDB[cite: 43]:
* **Input:** Storage-A reporta "unidades", Storage-B reporta "kilogramas".
* **Processo:** Aplicação da função em tempo real: `stock_units = raw_kg / 23`.
* **Output:** Métricas consistentes para o Dashboard e Coordenador.

---

## 🧠 Lógica de Decisão e Tolerância a Falhas

### Algoritmo de Atribuição (Coordinator)
[cite_start]O Coordenador utiliza uma heurística *greedy* para otimizar a frota[cite: 50, 51]:
1.  **Filtro:** Seleciona apenas robôs com `status="IDLE"` e `battery > 15%`.
2.  **Desempate:** Se existirem múltiplos candidatos, escolhe o que tiver maior nível de bateria.
3.  [cite_start]**Fila de Espera:** Se não houver *match*, a ordem vai para uma `PendingQueue` (FIFO), garantindo que nenhuma ordem é perdida durante picos de carga[cite: 54].

### Deteção de Anomalias (System Monitor)
O Monitor garante a fiabilidade analisando a telemetria e acionando o Gateway via UDP (Porta 9090) com um comando `FORCE_CHARGE` se:
* [cite_start]**Stall Detection:** O robô reporta `MOVING` mas a localização não muda há **30 segundos**[cite: 75].
* [cite_start]**Critical Battery:** A bateria cai abaixo de **15%** sem o robô estar a carregar[cite: 76].

---

## 📊 Visualização e Controlo (Grafana)

Os dados são persistidos no **InfluxDB Cloud** e visualizados em dois dashboards principais, conforme detalhado no relatório.

### 1. Fleet Overview (Vista Geral)
[cite_start]Proporciona um resumo de alto nível da operação[cite: 88].

![Fleet Overview](C:\Users\ivoro\OneDrive\Ambiente%20de%20Trabalho\LECD\2º%20Ano\SRSA\projeto\src\README%20IMAGES\Captura%20de%20ecrã%202025-12-09%200229.png)

* **Robot Status:** Tabela com o estado operacional e bateria de todos os AMRs ativos.
* **Latest Alerts:** Painel crítico ligado ao System Monitor. [cite_start]Regista eventos de alta prioridade (ex: *"Robot Stalled in SHELF-S1 for 0s"*, *"Battery Critical (15.0%)"*) e o tempo decorrido desde a deteção[cite: 91, 92].

### 2. Detailed Asset Analysis
[cite_start]Permite a inspeção granular de ativos específicos[cite: 95].

![Detailed Dashboard](C:\Users\ivoro\OneDrive\Ambiente%20de%20Trabalho\LECD\2º%20Ano\SRSA\projeto\src\README%20IMAGES\Captura%20de%20ecrã%202025-12-09%20023313.png)

* **State Timeline:** Visualiza o ciclo de trabalho (duty cycle). [cite_start]O código de cores distingue claramente `MOVING` (Verde), `PICKING`/`IDLE` (Amarelo) e `CHARGING` (Azul), validando a lógica determinística da máquina de estados[cite: 96, 97].
* [cite_start]**Task History:** Regista os comandos `EXECUTE_TASK` despachados pelo Coordenador[cite: 98].
* [cite_start]**Stock History:** Gráfico de séries temporais que revela o padrão **"dente de serra"** (sawtooth), confirmando o consumo durante o picking e o auto-reabastecimento rápido[cite: 99].
* [cite_start]**Station Throughput:** Linha temporal que indica os períodos de ocupação (`PROCESSING`) das estações de embalamento[cite: 100].

---

## 🚀 Instruções de Execução

### Pré-requisitos
* **Python 3.8+**
* **Broker:** Mosquitto (Configurado em `mqqtt_config.py`)
* **Bibliotecas:** `paho-mqtt`, `influxdb-client-3`

### 1. Iniciar Infraestrutura Central
Abra terminais distintos para cada serviço:

```bash
# Gateway (Ponte MQTT/UDP e InfluxDB)
python3 warehouse_gateway.py

# Coordinator (Gestor de Tarefas e Servidor UDP 9091)
python3 fleet_coordinator.py

# System Monitor (Monitorização de Saúde e Cliente UDP 9090)
python3 system_monitor.py

# (Opcional) Debugger de Rede
python3 mqtt_debugger.py
```

### 2. Iniciar Simuladores de Ativos
```bash
# Simular um Robô (Ex: AMR-1)
python3 amr_robot.py {GroupID} AMR-1

# Simular uma Prateleira (Ex: Zona A, S1)
python3 shelves.py {GroupID} storage-a S1 10

# Simular uma Estação
python3 loading_station.py {GroupID} P1
```
### 3. Injeção de Encomendas (Cliente) 
```bash
python3 client_order_injector.py --item item_A --qty 5 --station P1
```