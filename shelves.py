import sys
import time
import json
import random
import struct
import argparse
import paho.mqtt.client as mqtt
from datetime import datetime, timezone
from mqqtt_config import MQTT_BROKER as BROKER_ADDRESS, MQTT_PORT as BROKER_PORT, GROUP_ID


class Shelf:
    def __init__(self, group_id, zone_id, asset_id, update_time):
        self.group_id = group_id
        self.zone_id = zone_id
        self.asset_id = asset_id
        self.update_time = update_time
        self.config = self._get_initial_config()
        self.max_stock = self.config["stock"]
        # Subscreve a todos os status de robô para detetar "PICKING"
        self.topic_sub = f"warehouse/{group_id}/amr/+/status"
        self.topic_pub = f"warehouse/{group_id}/locations/{zone_id}/{asset_id}/status"

        self.topic = f"warehouse/{self.group_id}/locations/{self.zone_id}/{self.asset_id}/status"
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

    def _get_initial_config(self):
        """
        Define a configuração inicial da Prateleira com base na zona.
        A lógica de PACK_STATION foi removida.
        """
        config = {
            "asset id": self.asset_id,
            "type": "SHELF",
            "item id": "UNKNOWN",
            "stock": 0,
            "unit": "units"
        }

        if self.zone_id == "storage-a":
            config["item id"] = "item_A"
            config["unit"] = "units"
            config["stock"] = 100
        elif self.zone_id == "storage-b":
            config["item id"] = "item_C"
            config["unit"] = "kg"
            config["stock"] = 200

        # Qualquer outra zona, como "packing_zone", assumirá a configuração base acima,
        # mas idealmente este simulador só deve ser executado para zonas 'storage-a' ou 'storage-b'.
        return config

    def on_connect(self, client, userdata, flags, reason_code, properties):
        """Paho 2.1 Connection Callback"""
        if reason_code.is_failure:
            print(f"[{self.asset_id}] Falha na ligação: {reason_code}")
        else:
            print(f"[{self.asset_id}] Ligado ao Broker MQTT! (RC: {reason_code})")
            # Subscreve aos estados dos robôs
            self.client.subscribe(self.topic_sub)

    def on_message(self, client, userdata, msg):
        """
        Processa mensagens de estado dos robôs para reduzir o stock durante o PICKING.
        Toda a lógica de ignorar PACK_STATION foi removida.
        """
        # Se for uma prateleira de Armazenamento
        if self.config["type"] == "SHELF":
            try:
                payload = json.loads(msg.payload.decode())
                robot_location = payload.get("location_id", "")
                robot_status = payload.get("status", "")

                # Constrói o ID esperado (ex: Se sou S1, procuro por "SHELF-S1")
                # Nota: Deve garantir que o AMR usa "SHELF-S1" e não apenas "S1" na sua location_id.
                my_location_tag = f"SHELF-{self.asset_id}"

                # LÓGICA DE CONSUMO:
                # O robô está na minha localização E no estado "PICKING"?
                if robot_location == my_location_tag and robot_status == "PICKING":

                    amount_to_take = 5  # Valor fixo para simulação
                    if self.config["stock"] >= amount_to_take:
                        self.config["stock"] -= amount_to_take
                        print(f"[{self.asset_id}] ROBÔ DETETADO em PICKING! Stock reduzido para {self.config['stock']}")
                    else:
                        self.config["stock"] = 0
                        print(f"[{self.asset_id}] ROBÔ DETETADO! Stock esgotado.")
                    self.publish_status()  # Publica atualização imediata
            except Exception as e:
                # print(f"Error decoding message: {e}") # Descomentar para debug de erros
                pass
        # else: Não faz nada, pois não é uma SHELF de armazenamento

    def connect(self):
        """Liga-se ao broker e inicia o loop"""
        try:
            print(f"--- A iniciar Shelf {self.asset_id} ({self.zone_id}) ---")
            print(f"Tópico de publicação: {self.topic}")
            self.client.connect(BROKER_ADDRESS, BROKER_PORT, 200)
            self.client.loop_start()
        except Exception as e:
            print(f"Falha na Ligação: {e}")
            sys.exit(1)

    def refill(self):
        """Lógica de reabastecimento apenas para SHELF."""
        if self.config["type"] == "SHELF":
            if self.config["stock"] <= 0:
                print(f"[{self.asset_id}] Stock esgotado! A reabastecer em 2 segundos...")
                # Publica 0 antes de esperar
                self.config["stock"] = 0
                self.publish_status()
                time.sleep(2)  # Atraso de reabastecimento
                self.config["stock"] = self.max_stock
                print(f"[{self.asset_id}] Reabastecido para {self.max_stock}.")
                self.publish_status()

    def run(self):
        """Loop de execução principal (Non-blocking)"""
        self.connect()

        last_update_time = 0

        try:
            while True:
                current_time = time.time()

                # 1. VERIFICAÇÃO CRÍTICA (Executada constantemente)
                # Verifica o stock instantaneamente. Se for 0, reabastece logo.
                if self.config["stock"] <= 0:
                    self.refill()
                    # Resetamos o timer para forçar atualização após o refill
                    last_update_time = current_time

                    # 2. ATUALIZAÇÃO PERIÓDICA (Executada a cada X segundos)
                # Só publica o estado periódico se já passou o tempo definido
                if (current_time - last_update_time) >= self.update_time:
                    self.publish_status()
                    last_update_time = current_time

                # 3. PEQUENA PAUSA TÉCNICA
                # Dorme apenas 0.1s para não "fritar" o CPU (100% usage),
                # mas mantém a reatividade do sistema quase instantânea.
                time.sleep(0.1)

        except KeyboardInterrupt:
            print("A parar Sensor...")
            self.client.loop_stop()
            self.client.disconnect()

    def publish_status(self):
        """Publica o estado de configuração atual para o MQTT"""
        # Simplificado, pois agora só lida com SHELF
        payload = json.dumps(self.config)
        print(f"[{time.strftime('%H:%M:%S')}] A Publicar: {payload}")
        self.client.publish(self.topic, payload)


if __name__ == "__main__":

    parser = argparse.ArgumentParser(description="Simular uma Prateleira de Armazenamento.")
    parser.add_argument("group_id", help="O ID do grupo do armazém")
    parser.add_argument("zone_id", help="O ID único da zona onde esta prateleira está")
    parser.add_argument("asset_id", help="O ID único deste ativo (ex: S1, S2)")
    parser.add_argument("update_time", help="O intervalo de tempo entre as atualizações de estado (em segundos)",
                        type=float)
    args = parser.parse_args()

    # Uso de GROUP_ID importado do mqqtt_config para evitar duplicação (melhor prática)
    if args.group_id != GROUP_ID:
        print(f"Aviso: O group_id fornecido ({args.group_id}) é diferente do group_id no mqqtt_config.py ({GROUP_ID}).")

    shelf = Shelf(args.group_id, args.zone_id, args.asset_id, args.update_time)
    shelf.run()