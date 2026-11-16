"""Time-weighted average: the grid fill around the rows the TimescaleDB query answers.

The SQL itself (time_weight / interpolated_average) needs a TimescaleDB with the toolkit
and is not run here; these tests feed the rows that query would answer.
"""

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest
from beacontower.timeseries import TelemetryClient
from beacontower.timeseries._postgres import _TWA_COLUMNS, _fill_twa_bins
from beacontower.timeseries.datamodel import TimeseriesCredentials

HOUR = timedelta(hours=1)


def _t(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, 16, hour, minute, tzinfo=UTC)


def _row(bin_: datetime, value: float | None, cnt: int, first: tuple[datetime, float], last: tuple[datetime, float]) -> tuple:
    return (bin_, value, cnt, first[0], first[1], last[0], last[1])


def _frame(*rows: tuple) -> pd.DataFrame:
    return pd.DataFrame(list(rows), columns=_TWA_COLUMNS)


BINS = pd.date_range(_t(10), _t(14), freq=HOUR, inclusive="left")


def test_locf_holds_last_reading_over_empty_bins() -> None:
    # readings 10 at 10:00 and 40 at 10:45 -> 17.5 for the bin; 40 holds afterwards
    res = _frame(_row(_t(10), 17.5, 2, (_t(10), 10.0), (_t(10, 45), 40.0)))
    out = _fill_twa_bins(res, BINS, HOUR, "locf", default_if_none_value=-1)
    assert out["value"].tolist() == [17.5, 40.0, 40.0, 40.0]
    assert out["cnt"].tolist() == [2, 0, 0, 0]


def test_seed_fills_leading_bins_and_default_without_it() -> None:
    reading = _row(_t(12), 20.0, 1, (_t(12, 30), 20.0), (_t(12, 30), 20.0))
    seed = _row(_t(7), 5.0, 1, (_t(7, 15), 5.0), (_t(7, 15), 5.0))
    with_seed = _fill_twa_bins(_frame(seed, reading), BINS, HOUR, "locf", default_if_none_value=-1)
    assert with_seed["value"].tolist() == [5.0, 5.0, 20.0, 20.0]
    assert list(with_seed.index) == list(BINS)  # the seed's own bin is not returned
    without = _fill_twa_bins(_frame(reading), BINS, HOUR, "locf", default_if_none_value=-1)
    assert without["value"].tolist() == [-1.0, -1.0, 20.0, 20.0]


def test_linear_takes_the_line_at_the_middle_of_an_empty_bin() -> None:
    # 0 at 10:30 and 30 at 13:30: a line of 10 per hour; then held after the last reading
    res = _frame(
        _row(_t(10), 2.5, 1, (_t(10, 30), 0.0), (_t(10, 30), 0.0)),
        _row(_t(13), 27.5, 1, (_t(13, 30), 30.0), (_t(13, 30), 30.0)),
    )
    out = _fill_twa_bins(res, BINS, HOUR, "linear", default_if_none_value=-1)
    assert out["value"].tolist() == pytest.approx([2.5, 10.0, 20.0, 27.5])
    bins = pd.date_range(_t(13), _t(15), freq=HOUR, inclusive="left")
    assert _fill_twa_bins(res, bins, HOUR, "linear", default_if_none_value=-1)["value"].tolist() == [27.5, 30.0]


def test_lone_reading_without_an_average_is_its_value() -> None:
    res = _frame(_row(_t(11), None, 1, (_t(11, 20), 7.0), (_t(11, 20), 7.0)))
    out = _fill_twa_bins(res, BINS, HOUR, "linear", default_if_none_value=0)
    assert out["value"].tolist() == [0.0, 7.0, 7.0, 7.0]


def test_no_rows_gives_default_everywhere() -> None:
    out = _fill_twa_bins(_frame(), BINS, HOUR, "locf", default_if_none_value=3)
    assert out["value"].tolist() == [3.0] * 4
    assert out["cnt"].tolist() == [0] * 4


class _Cursor:
    def __init__(self, rows: list[tuple]) -> None:
        self.rows = rows
        self.params: dict = {}
        self.sql = ""

    def execute(self, query, params: dict) -> None:  # noqa: ANN001
        self.sql = query.as_string(None)
        self.params = params

    def fetchall(self) -> list[tuple]:
        return self.rows


def _client(rows: list[tuple]) -> tuple[TelemetryClient, _Cursor]:
    client = TelemetryClient(TimeseriesCredentials(host="h", port=1, database="d", login="l", password="p", table="telemetry"))
    cursor = _Cursor(rows)
    client.source._get_cursor = lambda **_: cursor  # type: ignore[method-assign]
    return client, cursor


def test_client_passes_method_and_returns_grid() -> None:
    client, cursor = _client([_row(_t(10), 17.5, 2, (_t(10), 10.0), (_t(10, 45), 40.0))])
    out = client.get_time_weighted_average("m", "a", "power", _t(10), _t(12), HOUR)
    assert cursor.params["how"] == "LOCF"
    assert "time_weight(" in cursor.sql
    assert "interpolated_average(" in cursor.sql
    assert out["value"].tolist() == [17.5, 40.0]

    series = client.get_time_weighted_average("m", "a", "power", _t(10), _t(12), HOUR, how="linear", return_count=False)
    assert cursor.params["how"] == "Linear"
    assert isinstance(series, pd.Series)


def test_client_refuses_unknown_method() -> None:
    client, _ = _client([])
    with pytest.raises(ValueError, match="how must be one of"):
        client.get_time_weighted_average("m", "a", "power", _t(10), _t(12), HOUR, how="spline")  # type: ignore[arg-type]


def test_do_twa_is_the_locf_time_weighted_average() -> None:
    client, cursor = _client([_row(_t(10), 17.5, 2, (_t(10), 10.0), (_t(10, 45), 40.0))])
    out = client.get_values_range("m", "a", "power", _t(10), _t(12), HOUR, do_twa=True)
    assert cursor.params["how"] == "LOCF"
    assert out["value"].tolist() == [17.5, 40.0]
    assert out["cnt"].tolist() == [2, 0]


def test_from_postgres_takes_fields_or_credentials() -> None:
    by_fields = TelemetryClient.from_postgres(host="h", login="l", password="p")
    assert by_fields.timeseries_credential.table == "asset_timeseries"
    creds = TimeseriesCredentials(host="h", login="l", password="p")
    assert TelemetryClient.from_postgres(creds).timeseries_credential is creds
    assert TelemetryClient(creds).timeseries_credential is creds


def test_a_client_needs_a_source() -> None:
    with pytest.raises(ValueError, match="from_api"):
        TelemetryClient()


def test_raw_values_are_the_readings() -> None:
    client, cursor = _client([(_t(10, 5), 1.5, 1), (_t(10, 20), 2.5, 1)])
    cursor.description = [type("C", (), {"name": n}) for n in ("time", "value", "cnt")]
    out = client.get_values_range("m", "a", "power", _t(10), _t(12), HOUR, raw_values=True)
    assert out["value"].tolist() == [1.5, 2.5]
    assert out.index.name == "time"
