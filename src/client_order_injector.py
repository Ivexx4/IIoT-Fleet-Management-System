import argparse
import json
import socket
from uuid import uuid4

from models import Order
from mqtt_config import UDP_HMAC_SECRET
from udp_auth import UdpAuthenticator


def send_order(
    item: str,
    quantity: int,
    station: str,
    host: str = "127.0.0.1",
    port: int = 9091,
) -> None:
    udp_auth = UdpAuthenticator(UDP_HMAC_SECRET)
    order = Order(
        order_id=uuid4().hex,
        item=item,
        quantity=quantity,
        pack_station=station,
    )
    payload = order.model_dump_json(exclude_none=True).encode("utf-8")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(1.0)
    try:
        for attempt in range(3):
            sock.sendto(udp_auth.seal(payload), (host, port))
            try:
                response, _ = sock.recvfrom(1024)
            except TimeoutError as error:
                if attempt == 2:
                    raise TimeoutError(
                        "Coordinator did not acknowledge the order"
                    ) from error
                continue
            response_payload = udp_auth.open(response)
            result = json.loads(response_payload.decode("utf-8"))
            if (
                result.get("order_id") == order.order_id
                and result.get("status") == "accepted"
            ):
                print(f"Order {order.order_id} accepted by {host}:{port}")
                return
            raise RuntimeError(f"Coordinator rejected the order: {result}")
    except (OSError, ValueError, TimeoutError) as error:
        print(f"Failed to submit order: {error}")
        raise
    finally:
        sock.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Simular Cliente de Armazém")
    parser.add_argument(
        "--item", default="item_A", help="ID do item (ex: item_A, item_C)"
    )
    parser.add_argument("--qty", type=int, default=1, help="Quantidade")
    parser.add_argument(
        "--station", default="P1", help="Estação de destino (ex: P1, P2)"
    )
    parser.add_argument("--host", default="127.0.0.1", help="Coordinator host")
    parser.add_argument("--port", type=int, default=9091, help="Coordinator UDP port")

    args = parser.parse_args()
    send_order(args.item, args.qty, args.station, args.host, args.port)
