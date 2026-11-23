"""The providers that bring devices' data into the platform (MQTT, IoT Hub, the data provider)."""

from _common import connect

bt = connect()
for provider in bt.get_providers():
    detail = bt.get_provider(provider.id)
    print(f"{detail.id}  {detail.display_name}  ({detail.type})")
