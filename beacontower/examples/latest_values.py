"""The newest reading of every numeric signal of an asset."""

from _common import NUMERIC, connect, find_signal

from beacontower.timeseries import TelemetryClient

bt = connect()
telemetry = TelemetryClient.from_beacon_tower_client(bt)
asset, _, newest = find_signal(bt, telemetry)

model = bt.get_model(asset.model_id)
print(f"{asset.display_name}, as of {newest:%Y-%m-%d %H:%M} UTC")
for interface in model.model_data:
    for name, signal in interface.telemetry.items():
        if signal.data_type in NUMERIC:
            # The default is what a signal without a reading gives; it must be a finite number.
            value = telemetry.get_latest_value(asset.model_id, asset.id, name, newest, default_value_if_none=0)
            print(f"  {name}: {value} {signal.unit or ''}")
