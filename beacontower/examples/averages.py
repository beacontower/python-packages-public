"""Hourly averages of a signal over a day, as a pandas DataFrame."""

from datetime import timedelta

from _common import connect, find_signal

from beacontower.timeseries import TelemetryClient

bt = connect()
telemetry = TelemetryClient.from_beacon_tower_client(bt)
asset, name, newest = find_signal(bt, telemetry)

hourly = telemetry.get_values_range(asset.model_id, asset.id, name, newest - timedelta(days=1), newest, timedelta(hours=1), do_average=True)
print(f"{asset.display_name} / {name}, hourly averages (value) and readings per hour (cnt):")
print(hourly)
