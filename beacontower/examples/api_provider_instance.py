"""Make API provider instances, read what they bring in, and remove them again. This WRITES.

It makes two instances for Malmö: the weather from Open-Meteo, and the day-ahead electricity
prices of bidding zone SE4 from ENTSO-E. For each it waits for the first poll, prints a few of
the newest values and removes the instance with its asset. They get ids of their own
(``example-...``), so an instance the environment already has is never touched.

Run it with ``--yes``; without it, it only says what it would do. Both sources must be
switched on in the environment (see api_provider_status.py).
"""

import sys
import time
from datetime import UTC, datetime

from _common import connect

from beacontower.apiproviders import ApiProvidersClient, Entsoe, OpenMeteo
from beacontower.timeseries import TelemetryClient

MALMO = OpenMeteo(latitude=55.605, longitude=13.0038)
# Sweden's bidding zones: "SE1" (Luleå), "SE2" (Sundsvall), "SE3" (Stockholm), "SE4" (Malmö).
SE4 = Entsoe(bidding_zone="SE4")

if "--yes" not in sys.argv:
    sys.exit(f"Would make, read and remove instances of {MALMO!r} and {SE4!r}. Run again with --yes.")

bt = connect()
providers = ApiProvidersClient(bt)
telemetry = TelemetryClient.from_beacon_tower_client(bt)
suffix = int(time.time())
for source in (MALMO, SE4):
    # In real use, ensure() reuses an existing instance of the same settings instead.
    instance = providers.create(source, instance_id=f"example-{source.name}-{suffix}")
    try:
        instance = providers.wait_for_first_poll(instance.id)
        print(f"{instance.id}: {instance.health}, asset {instance.asset_id}")
        model_id, asset_id = instance.model_id or "", instance.asset_id or instance.id
        for name in sorted(instance.units)[:3]:
            value = telemetry.get_latest_value(model_id, asset_id, name, datetime.now(UTC))
            print(f"  {name}: {value} {instance.units[name]}")
    finally:
        providers.remove(instance.id, delete_asset=True)
        print(f"Removed {instance.id} and its asset.")
