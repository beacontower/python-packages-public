"""What every example starts with: a login from the environment, and something to read.

Set ``BEACONTOWER_URL`` (the web client's address, such as ``https://example.beacontower.cloud``),
``BEACONTOWER_USER`` and ``BEACONTOWER_PASSWORD``. ``BEACONTOWER_VERIFY=false`` accepts a
self-signed certificate, as on a local test environment. The account must not use MFA: see
"Logging in" in the README.
"""

import os
import sys
from datetime import UTC, datetime, timedelta
from typing import NamedTuple

import pandas as pd

from beacontower.metadata import BeaconTowerClient
from beacontower.metadata.datamodel import Asset
from beacontower.timeseries import TelemetryClient

NUMERIC = {"double", "float", "integer", "long"}


def connect() -> BeaconTowerClient:
    """Log in with the address and account in the environment."""
    names = ("BEACONTOWER_URL", "BEACONTOWER_USER", "BEACONTOWER_PASSWORD")
    if missing := [name for name in names if not os.environ.get(name)]:
        sys.exit(f"Set {', '.join(missing)} first (see examples/README.md).")
    verify = os.environ.get("BEACONTOWER_VERIFY", "true").lower() not in {"false", "0", "no"}
    url, user, password = (os.environ[name] for name in names)
    return BeaconTowerClient(url, user, password, verify=verify)


class Signal(NamedTuple):
    """A numeric signal of an asset, and the end of the newest hour it has readings in."""

    asset: Asset
    name: str
    newest: datetime


def find_signal(bt: BeaconTowerClient, telemetry: TelemetryClient, lookback: timedelta = timedelta(days=7)) -> Signal:
    """Find a numeric signal with readings in `lookback` among the assets the login sees."""
    models = {model.id: model for model in bt.get_models()}
    now = datetime.now(UTC)
    for asset in bt.get_assets()[:20]:
        model = models.get(asset.model_id)
        if model is None:
            continue
        names = [name for i in model.model_data for name, t in i.telemetry.items() if t.data_type in NUMERIC]
        for name in names[:3]:
            hours = telemetry.get_values_range(asset.model_id, asset.id, name, now - lookback, now, timedelta(hours=1), do_average=True)
            if not isinstance(hours, pd.DataFrame):  # return_count=True, the default, gives a DataFrame
                raise TypeError(type(hours))
            seen = hours[hours["cnt"] > 0]
            if not seen.empty:
                newest = pd.Timestamp(seen.index.max())
                newest = newest.tz_localize(UTC) if newest.tzinfo is None else newest.tz_convert(UTC)
                return Signal(asset, name, newest.to_pydatetime() + timedelta(hours=1))
    sys.exit(f"No numeric signal with readings in the last {lookback} among the assets this login can see.")
