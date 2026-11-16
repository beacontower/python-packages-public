# beacontower

Python client for [Beacon Tower](https://github.com/beacontower): the assets, models,
devices and organizations of a tenant, and the timeseries its assets report.

```
pip install beacontower
```

## Metadata

```python
from beacontower.metadata import BeaconTowerClient

bt = BeaconTowerClient("https://<your-beacon-tower>", "<user name>", "<password>")
bt.check_auth()

assets = bt.get_assets()
model = bt.get_dtdl_model(assets[0].model_id)
```

The address is the one you open Beacon Tower at in a browser; the client finds the API
from it. Pass the API's own address with `resolve_url=False`.

## Timeseries

A `TelemetryClient` is created in one of two ways and used the same way afterwards.

```python
from datetime import UTC, datetime, timedelta
from beacontower.timeseries import TelemetryClient

# Through the timeseries API: the same address and login as above.
telemetry = TelemetryClient.from_api("https://<your-beacon-tower>", "<user name>", "<password>")

# Or straight from the TimescaleDB, for those with access to it
# (pip install "beacontower[postgres]").
telemetry = TelemetryClient.from_postgres(host="...", login="...", password="...")

hourly = telemetry.get_values_range(
    model_id=asset.model_id,
    asset_id=asset.id,
    timeseries_name="temperature",
    dt_start=datetime(2026, 11, 2, tzinfo=UTC),
    dt_end=datetime(2026, 11, 9, tzinfo=UTC),
    bin_size=timedelta(hours=1),
    do_average=True,
)
```

| Method | Answers |
|---|---|
| `get_latest_value` | the newest reading at or before a time |
| `get_values_range` | per bin: the average (`do_average`), the time-weighted average (`do_twa`), or by default the reading as of each bin label; `raw_values` gives the readings themselves |
| `get_time_weighted_average` | per bin, each reading weighed by how long it stood |
| `dump_values` | every reading of an asset in a period |
| `check_connection` | raises unless the source is reachable with the credentials |

An existing `BeaconTowerClient` can be reused: `TelemetryClient.from_beacon_tower_client(bt)`.

### Where the two sources differ

The methods, arguments and shapes are the same. The answers can differ in these cases:

| | Timeseries API | Database |
|---|---|---|
| Bin edges | A fixed grid: whole multiples of the bin size from a Monday at midnight UTC | Counted from `dt_start` |
| End of the period | Excluded | Included by the binned reads, which then drop the last bin; excluded by `raw_values` and the time-weighted average |
| Component | One, the root unless `component_id` is given | Every component together |
| Types | `telemetry` unless `types` is given | Every type together |
| NaN readings | Skipped | Make a bin's average NaN (the time-weighted average skips them) |
| Time-weighted average | `how="locf"` only, and only where the API has the function `twa` | `"locf"` and `"linear"`; needs the `timescaledb_toolkit` extension |
| `dump_values` `type` column | The timeseries type, such as `telemetry` | The value's kind, such as `doublevalue` |

With a `dt_start` on the API's grid, a period that ends on a bin edge, one component and
no NaN reading, the two give the same bins.

## License

MIT, see `LICENSE`.
