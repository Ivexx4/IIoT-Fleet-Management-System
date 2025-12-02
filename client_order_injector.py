import socket
import json
import argparse

# Configuração (deve bater certo com o fleet_coordinator.py)
UDP_IP = "127.0.0.1"
UDP_PORT = 9091  # [cite: 196]


def send_order(item, quantity, station):
    # Estrutura da encomenda definida no enunciado
    order = {
        "item": item,
        "quantity": quantity,
        "pack_station": station
    }

    message = json.dumps(order).encode('utf-8')

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.sendto(message, (UDP_IP, UDP_PORT))
        print(f"📦 Encomenda enviada para {UDP_IP}:{UDP_PORT}")
        print(f"   Conteúdo: {json.dumps(order, indent=2)}")
    except Exception as e:
        print(f"❌ Erro ao enviar: {e}")
    finally:
        sock.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Simular Cliente de Armazém")
    parser.add_argument("--item", default="item_A", help="ID do item (ex: item_A, item_C)")
    parser.add_argument("--qty", type=int, default=1, help="Quantidade")
    parser.add_argument("--station", default="P1", help="Estação de destino (ex: P1, P2)")

    args = parser.parse_args()
    send_order(args.item, args.qty, args.station)