"""Everything an asset reported in a quarter of an hour, every signal, one row per reading."""

from datetime import timedelta

from _common import connect, find_signal

from beacontower.timeseries import TelemetryClient

bt = connect()
telemetry = TelemetryClient.from_beacon_tower_client(bt)
asset, _, newest = find_signal(bt, telemetry)

rows = telemetry.dump_values(asset.model_id, asset.id, newest - timedelta(minutes=15), newest)
print(f"{asset.display_name}: {len(rows)} readings of {rows['timeseries_name'].nunique()} signals")
print(rows.head(10))
