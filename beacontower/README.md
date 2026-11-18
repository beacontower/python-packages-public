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
from it. The API's own address (`https://<host>/api/v1`) works as well; pass `resolve_url=False`
to have any address used exactly as given. For a deployment signed by its own
certificate authority, pass `verify="<path to its CA bundle>"`.

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

The methods, arguments and shapes are the same, and for one component of numeric
telemetry the two give the same frames. They were compared read by read against
timeseries API 0.2.15. The answers differ in these cases:

| | Timeseries API | Database |
|---|---|---|
| Time-weighted average after now | No value: a bin that starts after the current time gets `default_if_none_value`, also when it holds future-dated readings | The last reading is held, and future-dated readings are weighed |
| Time-weighted average, start of the bins | `dt_start` must lie on the API's grid (whole multiples of the bin size from a Monday at midnight UTC), else `ValueError` | Counted from any `dt_start` |
| Time-weighted average, `how` | `"locf"` only | `"locf"` and `"linear"`; needs the `timescaledb_toolkit` extension |
| Component | One, the root unless `component_id` is given | Every component together |
| Types | `telemetry` unless `types` is given | Every type together |
| `dump_values` `type` column | The timeseries type, such as `telemetry` | The value's kind, such as `doublevalue` |

Both skip readings that hold no number (NaN, or a non-numeric value), read the period as
`[dt_start, dt_end)`, weigh a last bin that runs past `dt_end` to its own end, and label
bins in UTC. The averages and the value-at-time read are cut on the same fixed grid by
both.

## License

MIT, see `LICENSE`.
