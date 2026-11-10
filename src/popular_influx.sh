#!/usr/bin/env bash

set -euo pipefail

GROUP_ID="${WAREHOUSE_GROUP_ID:-2024209219}"
PYTHON_CMD="${PYTHON_CMD:-python3}"
cd "$(dirname "$0")"

PIDS=()

start_process() {
    "$PYTHON_CMD" "$@" &
    PIDS+=("$!")
}

cleanup() {
    trap - EXIT INT TERM
    for pid in "${PIDS[@]}"; do
        kill "$pid" 2>/dev/null || true
    done
    for pid in "${PIDS[@]}"; do
        wait "$pid" 2>/dev/null || true
    done
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

start_process warehouse_gateway.py
sleep 2
start_process fleet_coordinator.py
start_process system_monitor.py

for shelf_id in {1..5}; do
    start_process shelves.py "$GROUP_ID" storage-a "S${shelf_id}" 2
done
for shelf_id in {6..10}; do
    start_process shelves.py "$GROUP_ID" storage-b "S${shelf_id}" 2
done

start_process loading_station.py "$GROUP_ID" P1
start_process loading_station.py "$GROUP_ID" P2
for robot_id in {1..4}; do
    start_process amr_robot.py "$GROUP_ID" "RBT${robot_id}"
done

echo "A simular fluxo variável de encomendas; Ctrl+C para parar."

COUNTER=1
while true; do
    if (( RANDOM % 100 < 70 )); then
        ITEM="item_A"
        QUANTITY=$((1 + RANDOM % 5))
    else
        ITEM="item_C"
        QUANTITY=$((1 + RANDOM % 4))
    fi
    STATION="P$((1 + RANDOM % 2))"

    echo "[Pedido $COUNTER] $ITEM x$QUANTITY -> $STATION"
    "$PYTHON_CMD" client_order_injector.py \
        --item "$ITEM" \
        --qty "$QUANTITY" \
        --station "$STATION"
    COUNTER=$((COUNTER + 1))

    # Inter-arrival times vary to avoid a fixed, perfectly periodic workload.
    sleep "$((8 + RANDOM % 13))"
done
