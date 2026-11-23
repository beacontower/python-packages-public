"""Make a data provider instance, read what it brings in, and remove it again. This WRITES.

It creates an ENTSO-E instance (day-ahead prices for one bidding zone, by default Finland),
waits for its first poll, prints the latest price and removes the instance with its asset.
Run it with ``--yes``; without it, it only says what it would do. ENTSO-E must be switched on
in the environment (see data_provider_status.py).
"""

import sys
from datetime import UTC, datetime

from _common import connect

from beacontower.dataproviders import DataProvidersClient, Entsoe
from beacontower.timeseries import TelemetryClient

source = Entsoe(bidding_zone="FI")
if "--yes" not in sys.argv:
    sys.exit(f"Would create, read and remove an instance of {source!r}. Run again with --yes.")

bt = connect()
providers = DataProvidersClient(bt)
instance = providers.create(source, wait=True)  # undone by itself if a step fails
try:
    print(f"Made {instance.id}: {instance.health}, asset {instance.asset_id}")
    telemetry = TelemetryClient.from_beacon_tower_client(bt)
    model_id, asset_id = instance.model_id or "", instance.asset_id or instance.id
    for name in sorted(instance.units)[:3]:
        print(f"  {name}: {telemetry.get_latest_value(model_id, asset_id, name, datetime.now(UTC))} {instance.units[name]}")
finally:
    providers.remove(instance.id, delete_asset=True)
    print(f"Removed {instance.id} and its asset.")
