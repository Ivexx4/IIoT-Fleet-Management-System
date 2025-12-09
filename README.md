# 🏭 Warehouse Automation System: A Hybrid IoT Architecture for AMR Coordination

Este projeto implementa uma solução completa de IoT Industrial (IIoT) para um armazém autónomo simulado, focada na gestão e coordenação de uma frota de **Robôs Móveis Autónomos (AMRs)** e ativos estáticos (Shelves e Packing Stations). [cite_start]O sistema utiliza uma arquitetura **híbrida e orientada a eventos**, combinando MQTT para telemetria de volume e UDP para sinalização crítica de baixa latência[cite: 620, 622].

## 💡 Arquitetura e Componentes

A arquitetura é desacoplada e utiliza um **Mosquitto MQTT Broker** como espinha dorsal. [cite_start]O design separa o *raw data* (sensores) da *business logic* (Coordenador)[cite: 627, 631]. 

| Componente | Ficheiro(s) | Função Principal |
| :--- | :--- | :--- |
| **Warehouse Gateway** | `warehouse_gateway.py` | **Tradutor Central**. [cite_start]Normaliza dados (e.g., convertendo $\text{kg} \to \text{unidades}$) [cite: 642][cite_start], arquiva em InfluxDB, e traduz comandos JSON em **payload binário de 3 bytes**[cite: 643, 646]. [cite_start]Atua como **Servidor UDP (porta 9090)** para alertas críticos[cite: 639, 654]. |
| **Fleet Coordinator** | `fleet_coordinator.py` | **Cérebro do Sistema**. [cite_start]Mantém o World State em tempo real [cite: 659] [cite_start]e executa a lógica de *Task Allocation* (selecionando robôs IDLE com bateria $>15\%$)[cite: 660, 664]. [cite_start]Recebe ordens via **Servidor UDP (porta 9091)**[cite: 639]. |
| **System Monitor** | `system_monitor.py` | **Monitor de Saúde**. Deteta anomalias CRÍTICAS (Bateria Baixa $<15\%$, Stall Detection) [cite: 687, 688, 689] e envia *overrides* (`FORCE_CHARGE`) via Cliente UDP[cite: 690]. |
| **AMR Robot Simulator** | `amr_robot.py` | Simula o ciclo de estado determinístico [cite: 669], decaimento de bateria ($1\%/\text{s}$ de estados ativos) [cite: 670], e falhas estocásticas (`STALLED`, $5\%$ prob.)[cite: 671]. [cite_start]Processa o comando de controlo binário[cite: 645]. |

---

## 🛠 Especificação de Protocolos

### A. Protocolo Binário (Robot Control)

[cite_start]Comandos enviados pelo Gateway aos AMRs através do tópico `/command` [cite: 636] [cite_start]num **payload fixo de 3 bytes** (`format string 'BBB'`) para processamento determinístico e eficiente[cite: 645, 646, 651].

| Byte | Significado | Descrição |
| :--- | :--- | :--- |
| **Byte 0** | Command Type | [cite_start]Identifica a ação (e.g., `0x01` para EXECUTE\_TASK, `0x03` para FORCE\_CHARGE)[cite: 648]. |
| **Byte 1** | Target Shelf ID | [cite_start]Carrega o ID numérico da prateleira (ex: 1 para S1)[cite: 649]. |
| **Byte 2** | Target Station ID | [cite_start]Carrega o ID numérico da estação (ex: 1 para P1)[cite: 650]. |

---

## 🚀 Como Executar

### Pré-requisitos
* **Python 3.8+**
* **Dependências:** `paho-mqtt 2.1`, `influxdb-client-3`
* **Broker MQTT:** Mosquitto (Endereço e Porta em `mqqtt_config.py`).

### 1. Configuração

1.  Defina o `GROUP_ID` em `mqqtt_config.py`.
2.  Preencha as credenciais do InfluxDB no `warehouse_gateway.py`.

### 2. Inicialização dos Serviços Centrais

Abra terminais separados para cada um:

```bash
# 1. Gateway (Servidor UDP 9090, Normalização, InfluxDB)
python3 warehouse_gateway.py

# 2. Coordinator (Servidor UDP 9091, Lógica de Atribuição)
python3 fleet_coordinator.py

# 3. System Monitor (Cliente UDP 9090, Deteção de Anomalias)
python3 system_monitor.py

# 4. MQTT Debugger (Ouvinte Geral)
python3 mqtt_debugger.py