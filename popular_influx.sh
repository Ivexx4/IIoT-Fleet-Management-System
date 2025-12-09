GROUP_ID="2024209219"
PYTHON_CMD="python3"

# Função para limpar processos ao fechar o script (Ctrl+C)
cleanup() {
    pkill -f "warehouse_gateway.py"
    pkill -f "fleet_coordinator.py"
    pkill -f "system_monitor.py"
    pkill -f "amr_robot.py"
    pkill -f "shelves.py"
    pkill -f "loading_station.py"
    exit
}

# Captura o sinal de saída (trap)
trap cleanup EXIT INT TERM

# 1. Iniciar Infraestrutura Central
$PYTHON_CMD warehouse_gateway.py &
PID_GW=$!
sleep 2 # Esperar que o Gateway ligue ao MQTT

$PYTHON_CMD fleet_coordinator.py &

echo "🛡️ A iniciar System Monitor..."
$PYTHON_CMD system_monitor.py  &

# 2. Iniciar Ativos Físicos (Sensores)
# args: group_id zone_id asset_id update_time
$PYTHON_CMD shelves.py 2024209219 storage-a S1 2 &
sleep 0.1
$PYTHON_CMD shelves.py 2024209219 storage-a S2 2 &
sleep 0.1
$PYTHON_CMD shelves.py 2024209219 storage-a S3 2 &
sleep 0.1
$PYTHON_CMD shelves.py 2024209219 storage-a S4 2 &
sleep 0.1
$PYTHON_CMD shelves.py 2024209219 storage-a S5 2 &
sleep 0.1
$PYTHON_CMD shelves.py 2024209219 storage-b S6 2 &
sleep 0.1
$PYTHON_CMD shelves.py 2024209219 storage-b S7 2 &
sleep 0.1
$PYTHON_CMD shelves.py 2024209219 storage-b S8 2 &
sleep 0.1
$PYTHON_CMD shelves.py 2024209219 storage-b S9 2 &
sleep 0.1
$PYTHON_CMD shelves.py 2024209219 storage-b S10 2 &
sleep 0.1
$PYTHON_CMD loading_station.py P1 &
sleep 0.1
$PYTHON_CMD loading_station.py P2 &
sleep 0.1
$PYTHON_CMD amr_robot.py 2024209219 RBT1 &
sleep 0.1
$PYTHON_CMD amr_robot.py 2024209219 RBT2 &
sleep 0.1
$PYTHON_CMD amr_robot.py 2024209219 RBT3 &
sleep 0.1
$PYTHON_CMD amr_robot.py 2024209219 RBT4 &
sleep 0.1

# 3. Loop de Injeção de Encomendas (População de Dados)
# Isto fará os robôs moverem-se, gastarem bateria e alterarem stocks.

echo "🔥 A iniciar injeção de encomendas automática..."
echo "   Pressiona Ctrl+C para parar."

COUNTER=1
while true; do
    echo "[Ciclo $COUNTER] A enviar encomendas..."

    $PYTHON_CMD client_order_injector.py --item item_A --qty 5 --station P1

    sleep 2

    # Encomenda 2: Item C (S2) -> P2 (Isto ativa a prateleira da zona storage-b)
    $PYTHON_CMD client_order_injector.py --item item_C --qty 10 --station P2
    
    ((COUNTER++))
done