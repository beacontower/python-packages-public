"""The telemetry read interface and a null implementation.

`Telemetry` is the structural contract code that reads binned telemetry can depend on --
fetch binned values for one (model, asset, timeseries) over a time range.
:class:`TelemetryClient` satisfies it against the timeseries API or TimescaleDB;
:class:`TelemetryUnavailable` is the stand-in for contexts that have no source. Typing
against `Telemetry` needs neither an HTTP client nor a database driver.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from datetime import datetime, timedelta

    import pandas as pd


class Telemetry(Protocol):
    """The read surface of a telemetry source.

    Any object providing :meth:`get_values_range` with this signature is a valid
    telemetry source -- it does not need to subclass anything (structural typing).
    """

    def get_values_range(  # noqa: PLR0913, PLR0917
        self,
        model_id: str,
        asset_id: str,
        timeseries_name: str,
        dt_start: datetime,
        dt_end: datetime,
        bin_size: timedelta,
        do_twa: bool = False,
        do_average: bool = False,
        raw_values: bool = False,
        return_count: bool = True,
        default_if_none_value: float = 0,
    ) -> pd.Series | pd.DataFrame:
        """Fetch binned values for one (model, asset, timeseries) over a time range."""


class TelemetryUnavailable:
    """A :class:`Telemetry` with no backing store: every read raises ``NotImplementedError``
    with the given reason. Use it where telemetry cannot be reached so callers fail loudly
    instead of silently getting defaults."""

    def __init__(self, reason: str) -> None:
        self._reason = reason

    def get_values_range(self, *_args: object, **_kwargs: object) -> pd.Series | pd.DataFrame:
        """Raise -- this telemetry source has no backing store."""
        raise NotImplementedError(self._reason)
