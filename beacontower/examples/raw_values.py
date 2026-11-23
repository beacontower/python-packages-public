"""Every reading of a signal in the last half hour, as stored."""

from datetime import timedelta

from _common import connect, find_signal

from beacontower.timeseries import TelemetryClient

bt = connect()
telemetry = TelemetryClient.from_beacon_tower_client(bt)
asset, name, newest = find_signal(bt, telemetry)

readings = telemetry.get_values_range(
    asset.model_id, asset.id, name, newest - timedelta(minutes=30), newest, timedelta(minutes=30), raw_values=True
)
print(f"{asset.display_name} / {name}: {len(readings)} readings in the last half hour")
print(readings.head(10))
