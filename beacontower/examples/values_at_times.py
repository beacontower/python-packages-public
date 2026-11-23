"""The value a signal had at fixed times: here every 30 minutes, the newest reading at each."""

from datetime import timedelta

from _common import connect, find_signal

from beacontower.timeseries import TelemetryClient

bt = connect()
telemetry = TelemetryClient.from_beacon_tower_client(bt)
asset, name, newest = find_signal(bt, telemetry)

# With no aggregation flag, each bin label gets the value as of that time.
values = telemetry.get_values_range(asset.model_id, asset.id, name, newest - timedelta(hours=3), newest, timedelta(minutes=30))
print(f"{asset.display_name} / {name}, value every 30 minutes:")
print(values)
