"""One telemetry client over two sources: the timeseries API or the database."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from beacontower.timeseries.datamodel import TimeseriesCredentials

if TYPE_CHECKING:
    import ssl
    from collections.abc import Sequence
    from datetime import datetime, timedelta

    import pandas as pd

    from beacontower.metadata.client import BeaconTowerClient
    from beacontower.timeseries._api import ApiTelemetry
    from beacontower.timeseries._postgres import PostgresTelemetry

TimeWeightMethod = Literal["locf", "linear"]


class TelemetryClient:
    """Reads telemetry from Beacon Tower.

    Create one with :meth:`from_api` (the timeseries API: an address, a user name and a
    password) or :meth:`from_postgres` (the TimescaleDB itself, which needs the
    ``postgres`` extra). Once created the two are used alike: the same methods, the same
    arguments and the same shape of answer. Where their answers can differ is listed in
    the package's README.
    """

    def __init__(
        self, timeseries_credential: TimeseriesCredentials | None = None, *, source: ApiTelemetry | PostgresTelemetry | None = None
    ) -> None:
        """Create a client on the database with `timeseries_credential` (as :meth:`from_postgres`).

        `source` is the reader a factory method made; pass one or the other.
        """
        if (timeseries_credential is None) == (source is None):
            raise ValueError("create a TelemetryClient with from_api(...) or from_postgres(...)")
        if timeseries_credential is not None:
            from beacontower.timeseries._postgres import PostgresTelemetry  # noqa: PLC0415 -- psycopg is an optional extra

            source = PostgresTelemetry(timeseries_credential)
        if source is None:  # unreachable; narrows the type
            raise ValueError("no source")
        self.source: ApiTelemetry | PostgresTelemetry = source
        self.timeseries_credential = timeseries_credential

    @classmethod
    def from_postgres(cls, credentials: TimeseriesCredentials | None = None, **fields: Any) -> TelemetryClient:
        """Read from TimescaleDB, given credentials or their fields.

        The fields are `host`, `login`, `password`, `port`, `database` and `table`.
        """
        return cls(credentials if credentials is not None else TimeseriesCredentials(**fields))

    @classmethod
    def from_api(  # noqa: PLR0913
        cls,
        url: str,
        user_name: str,
        password: str,
        *,
        resolve_url: bool = True,
        timeout: float = 60.0,
        verify: bool | str | ssl.SSLContext = True,
        types: Sequence[str] = ("telemetry",),
        component_id: str | None = None,
        match_model: bool = True,
    ) -> TelemetryClient:
        """Read through the timeseries API of the Beacon Tower at `url`.

        `url`, `user_name`, `password`, `resolve_url` and `verify` are those of
        :class:`beacontower.metadata.BeaconTowerClient`. `types` are the timeseries types
        read, `component_id` the component (the root when omitted), and `match_model`
        keeps a read to the rows written under exactly the model id it is given.
        """
        from beacontower.metadata.client import BeaconTowerClient  # noqa: PLC0415 -- keeps the database path free of httpx

        client = BeaconTowerClient(url, user_name, password, resolve_url=resolve_url, timeout=timeout, verify=verify)
        return cls.from_beacon_tower_client(client, types=types, component_id=component_id, match_model=match_model)

    @classmethod
    def from_beacon_tower_client(
        cls,
        client: BeaconTowerClient,
        *,
        types: Sequence[str] = ("telemetry",),
        component_id: str | None = None,
        match_model: bool = True,
    ) -> TelemetryClient:
        """Read through the timeseries API with an existing client's address and login."""
        from beacontower.timeseries._api import ApiTelemetry  # noqa: PLC0415

        return cls(source=ApiTelemetry(client, types=types, component_id=component_id, match_model=match_model))

    def check_connection(self) -> None:
        """Verify the source is reachable with the given credentials; raises when it is not."""
        self.source.check_connection()

    def do_rollback(self) -> None:
        """Roll back the database connection after a failed query. Does nothing on the API."""
        self.source.do_rollback()

    def do_reset(self) -> None:
        """Drop the database connection, or the API's access token; the next read starts afresh."""
        self.source.do_reset()

    def get_latest_value(
        self, model_id: str, asset_id: str, timeseries_name: str, dt: datetime, default_value_if_none: float = 0
    ) -> float | int:
        """Return the latest value at or before `dt`, falling back to `default_value_if_none`."""
        value: float | int = self.source.get_latest_value(model_id, asset_id, timeseries_name, dt, default_value_if_none)
        return value

    def dump_values(self, model_id: str, asset_id: str, dt_start: datetime, dt_end: datetime) -> pd.DataFrame:
        """Dump every reading of an asset in the period, as a long DataFrame, one row per reading.

        Columns: `model_id`, `asset_id`, `timeseries_name`, `time`, `insertion_time`,
        `type` and `value`.
        """
        return self.source.dump_values(model_id, asset_id, dt_start, dt_end)

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
        """Fetch binned telemetry for one (model, asset, timeseries) over [dt_start, dt_end].

        Aggregation priority is twa > average > raw > value-at-time (the first flag set
        wins). `do_twa` is :meth:`get_time_weighted_average` with ``how="locf"``. Returns a
        DataFrame with `value` and `cnt` when `return_count` is True, otherwise the `value`
        Series; gaps use `default_if_none_value`.
        """
        return self.source.get_values_range(
            model_id,
            asset_id,
            timeseries_name,
            dt_start,
            dt_end,
            bin_size,
            do_twa,
            do_average,
            raw_values,
            return_count,
            default_if_none_value,
        )

    def get_time_weighted_average(  # noqa: PLR0913, PLR0917
        self,
        model_id: str,
        asset_id: str,
        timeseries_name: str,
        dt_start: datetime,
        dt_end: datetime,
        bin_size: timedelta,
        how: TimeWeightMethod = "locf",
        return_count: bool = True,
        default_if_none_value: float = 0,
    ) -> pd.Series | pd.DataFrame:
        """Time-weighted average per bin for one numeric telemetry over [dt_start, dt_end).

        Each reading is weighed by how long it stood, so unevenly reported telemetry is
        averaged over time and not over readings. `how` says what holds between two
        readings: ``"locf"`` (default) holds a reading until the next one, ``"linear"``
        draws a line between them (database only). Returns `value` and `cnt` when
        `return_count` is True, otherwise the `value` Series.
        """
        return self.source.get_time_weighted_average(
            model_id, asset_id, timeseries_name, dt_start, dt_end, bin_size, how, return_count, default_if_none_value
        )
