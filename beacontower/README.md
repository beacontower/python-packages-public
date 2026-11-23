# beacontower

Python client for [Beacon Tower](https://github.com/beacontower): the assets, models,
devices and organizations of a tenant, the timeseries its assets report, and the data
provider's sources and their instances.

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

The value a read falls back to (`default_if_none_value`, `default_value_if_none`) must be a finite
number, an int or a float. NaN, infinity or anything else raises `ValueError` or `TypeError` before
anything is read. Count empty bins with `cnt`, which `return_count=True` gives.

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

### Gateways without `timeseries/query`

Gateways released before the timeseries API's `timeseries/query` was routed answer it
404. The client then reads through `timeseries/get` and `timeseries/get/last` instead,
until `do_reset`, and bins the readings itself as the API bins them.

This fallback is temporary: it will be removed from a later version of this package,
once gateways route `timeseries/query` (gateway 0.7.0-prod.5 and later do). Each switch to
it is logged as a warning on the `beacontower.timeseries` logger, which, like all of this
package's logging, is shown once the application configures logging (for instance with
`logging.basicConfig()`).

The frames are the same, as compared read by read against timeseries API 0.2.15, except
here:

| | Through `timeseries/get` |
|---|---|
| Model version | Not kept to: the readings of every model id are read, as with `match_model=False` |
| Component | A signal that a component other than the one read also reports is refused (`ValueError`) |
| Types | `telemetry` and `propertyReported` only; properties need timeseries API 0.2.16 |
| `dump_values` | Refused (`NotImplementedError`) |
| The reading before a period | Looked for at most ten years back |
| Now, for the time-weighted average | The client's clock |

A read takes more requests this way: the signal's newest reading, the period (in parts
where it holds more readings than one answer does), and for the value as of a time or a
time-weighted average whose period does not start with a reading, the reading before it.

## Data providers

Beacon Tower's data provider brings in data no device measures: weather at a position
(Open-Meteo), day-ahead electricity prices in a bidding zone (ENTSO-E) and exchange rates
against SEK (the Riksbank). Each environment switches the sources on that it uses.
`DataProvidersClient` shows which ones are on, lists their instances, and makes and removes them,
all through the API.

```python
from beacontower.dataproviders import DataProvidersClient, Entsoe, OpenMeteo, Riksbank

providers = DataProvidersClient(bt)  # a BeaconTowerClient, as above

status = providers.status()
for source in status.sources:
    print(source.name, source.state, source.detail, source.devices)
# openmeteo enabled Open-Meteo (...), every 01:00:00 {'ok': 3, 'pending': 0, ...}

weather = providers.ensure(OpenMeteo(latitude=55.605, longitude=13.0038, history_days=30), wait=True)
for instance in providers.instances("openmeteo"):
    print(instance.id, instance.settings, instance.health, instance.last_success_at)

providers.remove(weather.id)  # stops polling; the asset and its data stay unless delete_asset=True
```

An instance is a provider client provisioned with the source's settings, plus an asset with
the same id on the source's model, bound to the client's signals. The asset holds the data:
read it like any asset's (`asset_id` and `model_id` are on the instance).

| Method | Does |
|---|---|
| `status()` | whether the provider is registered and reporting, and per source its state (`enabled`, `disabled`, `invalid`, or `unknown` without a status device), what it polls, its instances by health, and whether the platform has its model |
| `instances(source=None)` | every instance, or one source's, with its settings, locked identity, `config_error`, last poll, history and units |
| `get(id)`, `find(source)` | one instance, by id or by its identity settings (an Open-Meteo position, an ENTSO-E zone) |
| `create(source, ...)` | the client, the asset, the binding, then the provisioning; undone when a step fails; `wait=True` returns after the first successful poll |
| `ensure(source, ...)` | `find`, else `create`: Open-Meteo bills per position, so reuse one |
| `remove(id, delete_asset=False)` | unprovisions (the provider stops polling) and deletes the client |
| `wait_for_first_poll(id)` | raises `InstanceConfigError` with the provider's reason when it does not poll the instance |
| `create_status_device()` | once per environment: the client the provider reports the sources' states to |

Settings are checked before anything is sent: a position in range, history up to 3660 days,
Open-Meteo variables the model has a channel for, an instance id without a dot. A source's
identity settings (an Open-Meteo position, an ENTSO-E zone) are locked by the provider at the
first successful poll; to change them, remove the instance and make another. The platform's
`enabled` flag on a client does not stop the provider from polling it; `remove` does.

### Adding a source

Each source is a class. A new one subclasses `Source` with its tag name, its model and the
tags that identify an instance, and declares its settings as fields, named as its device tags
in snake_case. It registers itself: `instances()` reads it from then on, and `create` sends it.

```python
from typing import ClassVar
from beacontower.dataproviders import Source

class Nordpool(Source):
    name: ClassVar[str] = "nordpool"
    model_id: ClassVar[str] = "dtmi:dataproviders_nordpool;1.1"
    identity: ClassVar[tuple[str, ...]] = ("area",)

    area: str
```

A source the package has no class for is still listed by `status()` and `instances()`, its
settings as an `OtherSource` holding the tags as they are.

### Testing against an environment

`tests/dataproviders/test_dataproviders_live.py` runs against a real Beacon Tower when
`BEACONTOWER_URL` (the API's address) and `BEACONTOWER_TOKEN` (or `BEACONTOWER_USER` and
`BEACONTOWER_PASSWORD`) are set; it makes one ENTSO-E instance, reads its prices back and removes
it. `BEACONTOWER_VERIFY=false` accepts a self-signed certificate, and `BEACONTOWER_ORGANIZATION`
names the organization when the login sees several.

## Versions

Every merge to `main` is a release on PyPI. Versions follow calendar versioning,
`YYYY.M.D.Rev`: `2026.11.24.1` is the first release of that day, `2026.11.24.2` the second
(see `VERSIONING.md`). Each release is tagged `vYYYY.M.D.Rev` on GitHub, with its notes. An
install from a git checkout reports `0.0.0+local`, because there the commit is the version.

## License

MIT, see `LICENSE`.
