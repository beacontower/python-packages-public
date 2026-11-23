"""Read telemetry straight from TimescaleDB instead of the API, for those with database access.

Needs ``pip install "beacontower[postgres]"`` and, besides the API login, ``BEACONTOWER_PG_HOST``,
``BEACONTOWER_PG_LOGIN`` and ``BEACONTOWER_PG_PASSWORD`` (optionally ``BEACONTOWER_PG_PORT`` and
``BEACONTOWER_PG_DATABASE``). The API login still finds the asset and its model.
"""

import os
import sys
from datetime import timedelta

from _common import connect, find_signal

from beacontower.timeseries import TelemetryClient

if missing := [n for n in ("BEACONTOWER_PG_HOST", "BEACONTOWER_PG_LOGIN", "BEACONTOWER_PG_PASSWORD") if not os.environ.get(n)]:
    sys.exit(f"Set {', '.join(missing)} first.")

bt = connect()
database = TelemetryClient.from_postgres(
    host=os.environ["BEACONTOWER_PG_HOST"],
    login=os.environ["BEACONTOWER_PG_LOGIN"],
    password=os.environ["BEACONTOWER_PG_PASSWORD"],
    port=int(os.environ.get("BEACONTOWER_PG_PORT", "5432")),
    database=os.environ.get("BEACONTOWER_PG_DATABASE", "timescale"),
)
database.check_connection()
asset, name, newest = find_signal(bt, database)

# The same calls and the same shape of answer as through the API.
print(database.get_values_range(asset.model_id, asset.id, name, newest - timedelta(hours=6), newest, timedelta(hours=1), do_average=True))
