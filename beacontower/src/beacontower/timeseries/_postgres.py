"""Telemetry read straight from the TimescaleDB that Beacon Tower stores it in."""

from datetime import datetime, timedelta
from typing import Literal, get_args

import pandas as pd
import psycopg
from psycopg.sql import SQL, Identifier

from .datamodel import TimeseriesCredentials

# Exactly one numeric typed column is non-null per row, chosen by the telemetry's
# DTDL schema (double/float/integer/long). COALESCE reads whichever it is and casts
# to float8 -- a single value path for all numeric telemetry, with AVG/LOCF kept in
# one type (float8). Non-numeric telemetry (string/boolean/array/...) is unsupported;
# callers are expected to check the model up front so those never reach these queries.
_VALUE_COLUMNS = ("doublevalue", "floatvalue", "integervalue", "longvalue")
_VALUE = SQL("COALESCE({cols})::float8").format(cols=SQL(", ").join(Identifier(c) for c in _VALUE_COLUMNS))

_QUERY_RANGE_RAW = SQL("""
        SELECT time, {value} as value, 1 AS cnt FROM {table}
            WHERE modelid=%(model_id)s
                AND assetid=%(asset_id)s
                AND timeseries_name=%(timeseries_name)s
                AND time >= %(dt_start)s
                AND time < %(dt_end)s
            ORDER BY time
        """)
_QUERY_RANGE_BINNED_AVERAGE = SQL("""
        SELECT time_bucket_gapfill(%(interval_size)s,time,start=>%(dt_start)s) as bin,
                AVG({value}) as value,
                COUNT(*) as cnt
                FROM {table}
                WHERE modelid=%(model_id)s
                    AND assetid=%(asset_id)s
                    AND timeseries_name=%(timeseries_name)s
                    AND time >= %(dt_start)s
                    AND time <= %(dt_end)s
                GROUP BY bin
        """)
# True time-weighted average (TimescaleDB Toolkit's time_weight), one row per bin that
# holds a reading, plus one for each seed: the newest reading before dt_start, which tells
# the first bin what held at its start, and the first reading from dt_end on, which a
# Linear average needs to draw the line out of the last reading. interpolated_average
# weighs the whole bin, not only first-to-last reading: it starts from the previous
# summary's last reading and, for LOCF, holds the bin's last reading to the bin's end
# (for Linear it runs on to the next summary's first reading). Bins without a reading
# have no row; _fill_twa_bins fills them from the first/last readings returned here.
# NULL (non-numeric) and NaN rows are skipped.
_TWA_SERIES_FILTER = SQL("""modelid=%(model_id)s
                    AND assetid=%(asset_id)s
                    AND timeseries_name=%(timeseries_name)s
                    AND {value} IS NOT NULL
                    AND {value} <> 'NaN'::float8""")
_QUERY_RANGE_BINNED_TIME_WEIGHT = SQL("""
        WITH readings AS (
            (SELECT time, {value} AS value FROM {table}
                WHERE {series_filter}
                    AND time < %(dt_start)s::timestamptz
                ORDER BY time DESC LIMIT 1)
            UNION ALL
            (SELECT time, {value} AS value FROM {table}
                WHERE {series_filter}
                    AND time >= %(dt_start)s::timestamptz
                    AND time < %(dt_end)s::timestamptz)
            UNION ALL
            (SELECT time, {value} AS value FROM {table}
                WHERE {series_filter}
                    AND time >= %(dt_end)s::timestamptz
                ORDER BY time LIMIT 1)
        ), summaries AS (
            SELECT time_bucket(%(interval_size)s::interval, time, origin => %(dt_start)s::timestamptz) AS bin,
                    time_weight(%(how)s::text, time, value) AS tw,
                    COUNT(*) FILTER (WHERE time >= %(dt_start)s::timestamptz AND time < %(dt_end)s::timestamptz) AS cnt,
                    MIN(time) AS first_time,
                    FIRST(value, time) AS first_value,
                    MAX(time) AS last_time,
                    LAST(value, time) AS last_value
                FROM readings
                GROUP BY bin
        )
        SELECT bin,
                interpolated_average(
                    tw, bin, %(interval_size)s::interval,
                    LAG(tw) OVER (ORDER BY bin), LEAD(tw) OVER (ORDER BY bin)
                ) AS value,
                cnt, first_time, first_value, last_time, last_value
            FROM summaries
            ORDER BY bin
        """)
_QUERY_EXACT_VALUE_AT_TIME = SQL("""
        SELECT time_bucket_gapfill(%(interval_size)s,time,start=>%(dt_start)s) as bin,
                LOCF(LAST({value}, time)) as value,
                COUNT(*) as cnt
                FROM {table}
                WHERE modelid=%(model_id)s
                    AND assetid=%(asset_id)s
                    AND timeseries_name=%(timeseries_name)s
                    AND time >= %(dt_start)s
                    AND time <= %(dt_end)s
                GROUP BY bin
        """)  # NOTE: Needs to be shifted and shift interval/increase interval!
_QUERY_LATEST_VALUE = SQL("""
        SELECT {value} as value FROM {table}
                WHERE modelid=%(model_id)s
                    AND assetid=%(asset_id)s
                    AND timeseries_name=%(timeseries_name)s
                    AND time <= %(dt)s
                ORDER BY time DESC LIMIT 1""")
_QUERY_DUMP_ALL_VALUES = SQL("""
        SELECT * FROM {table}
                WHERE modelid=%(model_id)s
                    AND assetid=%(asset_id)s
                    AND time >= %(dt_start)s
                    AND time <= %(dt_end)s""")


TimeWeightMethod = Literal["locf", "linear"]
_TIME_WEIGHT_METHODS = {"locf": "LOCF", "linear": "Linear"}
_TWA_COLUMNS = ["bin", "value", "cnt", "first_time", "first_value", "last_time", "last_value"]


def _fill_twa_bins(
    res: pd.DataFrame, bins: pd.DatetimeIndex, bin_size: timedelta, how: TimeWeightMethod, default_if_none_value: float
) -> pd.DataFrame:
    """Put the rows of `_QUERY_RANGE_BINNED_TIME_WEIGHT` on the full grid `bins` (UTC).

    The query only answers bins that hold a reading (and the seed's bin, before the
    grid). A bin without a reading takes the value that held across it: with ``locf`` the
    last reading before it, with ``linear`` the line between the readings on either side,
    taken at the bin's middle (after the last reading there is no line, so it is held).
    Bins before the series' first reading get `default_if_none_value`, with `cnt` 0.
    """
    res = res.copy()
    for column in ("bin", "first_time", "last_time"):
        res[column] = pd.to_datetime(res[column], utc=True)
    for column in ("value", "first_value", "last_value"):
        res[column] = res[column].astype(float)
    res = res.set_index("bin").sort_index()
    # A lone reading with nothing to weigh it against (linear, no neighbours) has no average.
    res["value"] = res["value"].fillna(res["last_value"])

    every = bins.union(res.index)
    before = res[["last_time", "last_value"]].reindex(every).ffill()
    fill = before["last_value"]
    if how == "linear":
        after = res[["first_time", "first_value"]].reindex(every).bfill()
        middle = every.to_series() + pd.Timedelta(bin_size) / 2
        share = (middle - before["last_time"]) / (after["first_time"] - before["last_time"])
        fill = (before["last_value"] + share * (after["first_value"] - before["last_value"])).fillna(fill)

    out = pd.DataFrame(index=bins)
    out.index.name = "bin"
    out["value"] = res["value"].reindex(bins).fillna(fill.reindex(bins)).fillna(default_if_none_value)
    out["cnt"] = res["cnt"].reindex(bins).fillna(0).astype(int)
    return out


class PostgresTelemetry:
    """Reads telemetry from TimescaleDB. Use it through ``TelemetryClient.from_postgres``."""

    def __init__(self, timeseries_credential: TimeseriesCredentials) -> None:
        """Create a reader from the given TimescaleDB credentials."""
        self.timeseries_credential = timeseries_credential
        self.conn: psycopg.Connection | None = None
        self.cursor: psycopg.Cursor | None = None

    def _get_connection(self, rollback: bool = False, reset: bool = False) -> psycopg.Connection:
        """Return the cached connection, opening it on first use.

        Roll back or reset the connection first when requested.
        """
        if reset:
            self.conn = None
            self.cursor = None

        if self.conn is None:
            conn_string = (
                f"host={self.timeseries_credential.host} "
                f"port={self.timeseries_credential.port} "
                f"dbname={self.timeseries_credential.database} "
                f"user={self.timeseries_credential.login} "
                f"password={self.timeseries_credential.password} "
                "target_session_attrs=read-write"
            )
            self.conn = psycopg.connect(conn_string)
        if rollback:
            self.conn.rollback()
        return self.conn

    def _get_cursor(self, rollback: bool = False, reset: bool = False) -> psycopg.Cursor:
        """Return the cached cursor, opening a connection and cursor on first use.

        Roll back or reset first when requested. Raises a ``TypeError`` if the opened
        cursor is not a ``psycopg.Cursor``.
        """
        if (self.cursor is None) | rollback | reset:
            conn = self._get_connection(rollback=rollback, reset=reset)
            self.cursor = conn.cursor()
        if not isinstance(self.cursor, psycopg.Cursor):
            raise TypeError(f"Cursor is of type {type(self.cursor)} but should be psycopg.Cursor")
        return self.cursor

    def do_rollback(self) -> None:
        """Rollback database connection."""
        self._get_connection(rollback=True)

    def do_reset(self) -> None:
        """Reset database connection."""
        self._get_connection(reset=True)

    def check_connection(self) -> None:
        """Verify the database is reachable with the given credentials.

        Runs a trivial query; psycopg raises a clear error (bad host/port/login/
        password, or an unreachable port-forward) if anything is wrong."""
        cursor = self._get_cursor()
        cursor.execute("SELECT 1")
        cursor.fetchone()

    def get_latest_value(
        self, model_id: str, asset_id: str, timeseries_name: str, dt: datetime, default_value_if_none: float = 0
    ) -> float | int:
        """Return the latest value at or before `dt`, falling back to `default_value_if_none`."""
        cursor = self._get_cursor()
        cursor.execute(
            _QUERY_LATEST_VALUE.format(table=Identifier(self.timeseries_credential.table), value=_VALUE),
            {"model_id": model_id, "asset_id": asset_id, "timeseries_name": timeseries_name, "dt": dt},
        )
        res = cursor.fetchone()
        if res is None:
            return default_value_if_none
        value: float | int = res[0]
        return value

    def dump_values(self, model_id: str, asset_id: str, dt_start: datetime, dt_end: datetime) -> pd.DataFrame:
        """Dump all values for an asset over [dt_start, dt_end].

        Returns a long DataFrame with one row per reading.
        """
        cursor = self._get_cursor()
        cursor.execute(
            _QUERY_DUMP_ALL_VALUES.format(table=Identifier(self.timeseries_credential.table)),
            {
                "model_id": model_id,
                "asset_id": asset_id,
                "dt_start": dt_start,
                "dt_end": dt_end,
            },
        )
        res = pd.DataFrame(cursor.fetchall(), columns=[col.name for col in cursor.description or []]).sort_values(by="time")
        result = []
        for _, row in res.iterrows():
            result_row = {
                "model_id": row.modelid,
                "asset_id": row.assetid,
                "timeseries_name": row.timeseries_name,
                "time": row.time,
                "insertion_time": row.insertion_time,
            }
            for column in [
                "booleanarrayvalue",
                "floatarrayvalue",
                "booleanvalue",
                "integerarrayvalue",
                "datearrayvalue",
                "durationarrayvalue",
                "complexarrayvalue",
                "stringarrayvalue",
                "datetimevalue",
                "doublevalue",
                "durationvalue",
                "floatvalue",
                "integervalue",
                "longvalue",
                "complexvalue",
                "longarrayvalue",
                "doublearrayvalue",
                "stringvalue",
            ]:
                if not pd.isna(row[column]):
                    result_row["type"] = column
                    result_row["value"] = row[column]
                    result.append(result_row)
        return pd.DataFrame(result)

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
        wins). Returns a DataFrame with `value` and `cnt` when `return_count` is True,
        otherwise a `value` Series; gaps use `default_if_none_value`.

        `do_twa` is :meth:`get_time_weighted_average` with ``how="locf"``: a true
        time-weighted average, with bins labelled in UTC.
        """
        if do_twa:
            return self.get_time_weighted_average(
                model_id=model_id,
                asset_id=asset_id,
                timeseries_name=timeseries_name,
                dt_start=dt_start,
                dt_end=dt_end,
                bin_size=bin_size,
                how="locf",
                return_count=return_count,
                default_if_none_value=default_if_none_value,
            )
        cursor = self._get_cursor()
        if do_average:
            cursor.execute(
                _QUERY_RANGE_BINNED_AVERAGE.format(table=Identifier(self.timeseries_credential.table), value=_VALUE),
                {
                    "interval_size": bin_size,
                    "dt_start": dt_start,
                    "model_id": model_id,
                    "asset_id": asset_id,
                    "timeseries_name": timeseries_name,
                    "dt_end": dt_end,
                },
            )
            res = (
                pd.DataFrame(cursor.fetchall(), columns=[col.name for col in cursor.description or []])
                .set_index("bin")
                .sort_index()
                .iloc[:-1]
            )
            res["value"] = res["value"].astype(float).fillna(default_if_none_value)
        elif raw_values:
            cursor.execute(
                _QUERY_RANGE_RAW.format(table=Identifier(self.timeseries_credential.table), value=_VALUE),
                {
                    "dt_start": dt_start,
                    "model_id": model_id,
                    "asset_id": asset_id,
                    "timeseries_name": timeseries_name,
                    "dt_end": dt_end,
                },
            )
            res = pd.DataFrame(cursor.fetchall(), columns=[col.name for col in cursor.description or []]).set_index("time").sort_index()
            res["value"] = res["value"].astype(float)
        else:
            previous_value = self.get_latest_value(
                model_id=model_id,
                asset_id=asset_id,
                timeseries_name=timeseries_name,
                dt=dt_start,
                default_value_if_none=default_if_none_value,
            )
            cursor.execute(
                _QUERY_EXACT_VALUE_AT_TIME.format(
                    table=Identifier(self.timeseries_credential.table),
                    value=_VALUE,
                ),
                {
                    "interval_size": bin_size,
                    "dt_start": dt_start - bin_size,
                    "model_id": model_id,
                    "asset_id": asset_id,
                    "timeseries_name": timeseries_name,
                    "dt_end": dt_end - bin_size,
                    "previous_value": previous_value,
                },
            )
            res = pd.DataFrame(cursor.fetchall(), columns=[col.name for col in cursor.description or []]).set_index("bin").sort_index()
            res.index = res.index + bin_size
            res["value"] = res["value"].astype(float).fillna(previous_value)
        if return_count:
            res["cnt"] = res["cnt"].astype(float).fillna(0).astype(int)
            return res
        return res["value"]

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
        draws a line between them. A bin starts from the newest reading before it, however
        far back; bins before the series' first reading get `default_if_none_value`.

        Bins are `bin_size` wide, counted from `dt_start` and labelled by their start in
        UTC (a naive `dt_start`/`dt_end` is read as UTC). `cnt` is the number of readings
        in the bin. Returns `value` and `cnt` when `return_count` is True, otherwise the
        `value` Series. Needs the ``timescaledb_toolkit`` extension in the database.
        """
        if how not in get_args(TimeWeightMethod):
            raise ValueError(f"how must be one of {get_args(TimeWeightMethod)}, got {how!r}")
        start, end = (pd.Timestamp(dt) for dt in (dt_start, dt_end))
        start, end = (dt.tz_localize("UTC") if dt.tzinfo is None else dt.tz_convert("UTC") for dt in (start, end))
        bins = pd.date_range(start=start, end=end, freq=bin_size, inclusive="left")

        cursor = self._get_cursor()
        cursor.execute(
            _QUERY_RANGE_BINNED_TIME_WEIGHT.format(
                table=Identifier(self.timeseries_credential.table),
                value=_VALUE,
                series_filter=_TWA_SERIES_FILTER.format(value=_VALUE),
            ),
            {
                "interval_size": bin_size,
                "dt_start": start.to_pydatetime(),
                "dt_end": end.to_pydatetime(),
                "model_id": model_id,
                "asset_id": asset_id,
                "timeseries_name": timeseries_name,
                "how": _TIME_WEIGHT_METHODS[how],
            },
        )
        res = _fill_twa_bins(
            pd.DataFrame(cursor.fetchall(), columns=_TWA_COLUMNS),
            bins=bins,
            bin_size=bin_size,
            how=how,
            default_if_none_value=default_if_none_value,
        )
        if return_count:
            return res
        return res["value"]
