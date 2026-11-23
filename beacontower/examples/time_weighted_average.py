"""Time-weighted averages: each reading counts for as long as it stood, not once."""

from datetime import timedelta

from _common import connect, find_signal

from beacontower.timeseries import TelemetryClient

bt = connect()
telemetry = TelemetryClient.from_beacon_tower_client(bt)
asset, name, newest = find_signal(bt, telemetry)

# "locf" holds a reading until the next one; "linear" (the database only) draws a line between them.
weighted = telemetry.get_time_weighted_average(asset.model_id, asset.id, name, newest - timedelta(hours=6), newest, timedelta(hours=1))
print(f"{asset.display_name} / {name}, time-weighted hourly averages:")
print(weighted)
