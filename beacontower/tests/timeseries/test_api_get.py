"""The API reader on a gateway that does not route timeseries/query: its reads made of timeseries/get."""

import logging
from datetime import UTC, datetime, timedelta

import httpx
import pandas as pd
import pytest
from beacontower.timeseries import TelemetryClient

HOUR = timedelta(hours=1)


def _t(day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=UTC)


def _z(time: datetime | pd.Timestamp) -> str:
    return pd.Timestamp(time).strftime("%Y-%m-%dT%H:%M:%SZ")


def _refuse(path: str, status: int, text: str = "") -> None:
    request = httpx.Request("POST", f"http://gateway/{path}")
    raise httpx.HTTPStatusError("refused", request=request, response=httpx.Response(status, text=text, request=request))


class _Gateway:
    """Stands in for BeaconTowerClient on a gateway that routes timeseries/get and get/last but not timeseries/query.

    It answers get and get/last from `readings`, (time, value[, type[, component]]) of signal
    `power` of asset `a`, as the API does: get reads [searchSpanFrom, searchSpanTo] of every
    component, and at its row `limit` answers that many rows in no order and says so.
    """

    def __init__(self, readings=(), limit: int = 10_000, routes_query: bool = False) -> None:
        self.readings = [(pd.Timestamp(r[0]), r[1], *(r[2:] or ("telemetry",)), *(r[3:] or (None,)))[:4] for r in readings]
        self.limit = limit
        self.routes_query = routes_query
        self.posts: list[tuple[str, dict]] = []
        self.header = {"Authorization": "Bearer t"}

    def spans(self) -> list[tuple[str, str]]:
        return [(body["searchSpanFrom"], body["searchSpanTo"]) for path, body in self.posts if path == "timeseries/get"]

    def paths(self) -> list[str]:
        return [path for path, _ in self.posts]

    def _post_json(self, path: str, *, json: dict) -> dict:
        self.posts.append((path, json))
        if path == "timeseries/query":
            if self.routes_query:
                return {"valueAt": [{"assetId": "a", "name": "power", "type": "telemetry", "items": [{"value": 99.0}]}]}
            _refuse(path, 404)
        if path == "timeseries/get/last":
            item = json["items"][0]
            assert item["assetId"] == "a" and "componentId" not in item
            types = {t["type"] for t in item["timeseries"] if t["name"] == "power"}
            newest: dict = {}
            for time, value, type_, component in self.readings:
                if type_ in types and ((component, type_) not in newest or time >= newest[component, type_][0]):
                    newest[component, type_] = (time, value)
            return {
                "items": [
                    {"timestamp": _z(t), "assetId": "a", "componentId": c, "timeseriesName": "power", "timeseriesType": ty, "value": v}
                    for (c, ty), (t, v) in newest.items()
                ]
            }
        assert path == "timeseries/get"
        assert json["assetId"] == "a" and json["timeseriesName"] == ["power"]
        start, end = pd.Timestamp(json["searchSpanFrom"]), pd.Timestamp(json["searchSpanTo"])
        assert start <= end and end - start <= pd.Timedelta(days=366)
        rows = [r for r in self.readings if start <= r[0] <= end and r[2] in json["types"]]
        cut = len(rows) > self.limit
        rows = rows[::-1][: self.limit] if cut else rows
        types = sorted({r[2] for r in rows}) or ["telemetry"]
        return {
            "items": [
                {
                    "name": "power",
                    "signalId": "power",
                    "type": type_,
                    "items": [{"timestamp": _z(r[0]), "value": r[1]} for r in rows if r[2] == type_],
                    "queryResultLimitReached": {"resultLimit": self.limit} if cut else None,
                }
                for type_ in types
            ]
        }


def _client(gateway: _Gateway, **options) -> TelemetryClient:
    return TelemetryClient.from_beacon_tower_client(gateway, **options)


def test_a_404_query_falls_back_to_get_once_and_reset_asks_again() -> None:
    gateway = _Gateway([(_t(16, 9), 5.0)])
    client = _client(gateway)
    assert client.get_latest_value("m", "a", "power", _t(16, 10)) == 5.0
    assert client.get_latest_value("m", "a", "power", _t(16, 10)) == 5.0
    assert gateway.paths() == ["timeseries/query", "timeseries/get/last", "timeseries/get/last"]
    gateway.routes_query = True
    client.do_reset()
    assert client.get_latest_value("m", "a", "power", _t(16, 10)) == 99.0


def test_each_switch_to_get_is_logged_as_one_warning(caplog: pytest.LogCaptureFixture) -> None:
    def warnings() -> list[str]:
        return [r.getMessage() for r in caplog.records if r.name.startswith("beacontower.") and r.levelno >= logging.WARNING]

    gateway = _Gateway([(_t(16, 9), 5.0)])
    client = _client(gateway)
    with caplog.at_level(logging.WARNING, logger="beacontower.timeseries"):
        client.get_latest_value("m", "a", "power", _t(16, 10))
        client.get_values_range("m", "a", "power", _t(16), _t(16, 12), HOUR, do_average=True)
        client.get_time_weighted_average("m", "a", "power", _t(16), _t(16, 12), HOUR)
        assert len(warnings()) == 1
        assert "does not route timeseries/query" in warnings()[0]
        assert "temporary" in warnings()[0]
        assert "0.7.0-prod.5" in warnings()[0]
        client.do_reset()  # the gateway is asked again, and the second switch is logged again
        client.get_latest_value("m", "a", "power", _t(16, 10))
        assert len(warnings()) == 2
        gateway.routes_query = True
        client.do_reset()
        client.get_latest_value("m", "a", "power", _t(16, 10))
        assert len(warnings()) == 2


def test_average_bins_on_the_grid_as_the_query_does() -> None:
    gateway = _Gateway(
        [
            (_t(16, 9, 59), 100.0),  # before the period
            (_t(16, 10, 15), 1.0),
            (_t(16, 10, 45), 2),
            (_t(16, 11, 10), "on"),  # not a number
            (_t(16, 11, 20), None),  # NaN, as the API answers it
            (_t(16, 11, 30), True),
            (_t(16, 12, 0), 50.0),  # at dt_end
        ]
    )
    out = _client(gateway).get_values_range("m", "a", "power", _t(16, 10), _t(16, 12), HOUR, do_average=True, default_if_none_value=7)
    assert gateway.spans() == [("2026-11-16T10:00:00.000000Z", "2026-11-16T11:59:59.999999Z")]
    assert gateway.posts[-1][1]["types"] == ["telemetry"]
    assert out["value"].tolist() == [1.5, 7.0]
    assert out["cnt"].tolist() == [2, 0]
    assert list(out.index) == [pd.Timestamp(_t(16, 10)), pd.Timestamp(_t(16, 11))]
    assert out.index.name == "bin"
    assert (out["value"].dtype, out["cnt"].dtype) == (float, int)


def test_a_period_off_the_grid_has_its_first_bin_labelled_on_the_grid() -> None:
    gateway = _Gateway([(_t(16, 10, 15), 1.0), (_t(16, 10, 45), 3.0), (_t(16, 11, 15), 5.0)])
    out = _client(gateway).get_values_range("m", "a", "power", _t(16, 10, 30), _t(16, 11, 30), HOUR, do_average=True)
    assert list(out.index) == [pd.Timestamp(_t(16, 10)), pd.Timestamp(_t(16, 11))]
    assert out["value"].tolist() == [3.0, 5.0]


def test_value_at_time_starts_from_the_reading_before_the_period() -> None:
    gateway = _Gateway([(_t(16, 8), 1.0), (_t(16, 9, 30), 4.0), (_t(16, 10, 30), 6.0), (_t(16, 11, 30), 8.0)])
    out = _client(gateway).get_values_range("m", "a", "power", _t(16, 10), _t(16, 12), HOUR)
    # Bins of [09:00, 12:00), labelled by their end: the reading as of each label.
    assert gateway.spans()[0] == ("2026-11-16T09:00:00.000000Z", "2026-11-16T11:59:59.999999Z")
    assert list(out.index) == [pd.Timestamp(_t(16, h)) for h in (10, 11, 12)]
    assert out["value"].tolist() == [4.0, 6.0, 8.0]
    assert out["cnt"].tolist() == [1, 1, 1]


def test_the_reading_before_the_period_is_looked_for_further_back() -> None:
    gateway = _Gateway([(_t(5, 12), 3.0), (_t(16, 10, 30), 6.0)])
    out = _client(gateway).get_values_range("m", "a", "power", _t(16, 10), _t(16, 12), HOUR, return_count=False)
    assert out.tolist() == [3.0, 6.0, 6.0]
    # The period, then back from its start: a day, 8 days, and 64 days, which hold the reading on the 5th.
    assert gateway.spans() == [
        ("2026-11-16T09:00:00.000000Z", "2026-11-16T11:59:59.999999Z"),
        ("2026-11-15T09:00:00.000000Z", "2026-11-16T08:59:59.999999Z"),
        ("2026-11-07T09:00:00.000000Z", "2026-11-15T08:59:59.999999Z"),
        ("2026-09-04T09:00:00.000000Z", "2026-11-07T08:59:59.999999Z"),
    ]


def test_looking_back_stops_ten_years_back() -> None:
    gateway = _Gateway([(datetime(2016, 11, 1, tzinfo=UTC), 1.0), (_t(16, 10, 30), 6.0)])
    assert _client(gateway).get_latest_value("m", "a", "power", _t(16, 10), default_value_if_none=-1) == -1
    spans = gateway.spans()
    assert spans[-1][0] == "2016-11-15T10:00:00.000001Z"
    assert len(spans) == 13  # a day, 8 days, 64 days, then a year at a time


def test_no_look_back_when_the_period_starts_with_a_reading() -> None:
    gateway = _Gateway([(_t(16, 9), 1.0), (_t(16, 10), 2.0), (_t(16, 11), 3.0)])
    client = _client(gateway)
    assert client.get_values_range("m", "a", "power", _t(16, 10), _t(16, 12), HOUR, do_twa=True)["value"].tolist() == [2.0, 3.0]
    assert client.get_values_range("m", "a", "power", _t(16, 11), _t(16, 12), HOUR)["value"].tolist() == [2.0, 3.0]
    assert len(gateway.spans()) == 2  # the periods only


def test_a_series_without_readings_gets_the_default_without_looking_back() -> None:
    gateway = _Gateway()
    client = _client(gateway)
    out = client.get_values_range("m", "a", "power", _t(16, 10), _t(16, 12), HOUR, default_if_none_value=-1)
    assert out["value"].tolist() == [-1.0, -1.0, -1.0]
    assert out["cnt"].tolist() == [0, 0, 0]
    assert client.get_latest_value("m", "a", "power", _t(16, 10), default_value_if_none=-2) == -2
    assert len(gateway.spans()) == 1


def test_time_weighted_average_holds_each_reading_until_the_next() -> None:
    gateway = _Gateway([(_t(16, 9), 2.0), (_t(16, 10, 30), 4.0), (_t(16, 11), 6.0), (_t(16, 11, 45), 10.0)])
    out = _client(gateway).get_values_range("m", "a", "power", _t(16, 10), _t(16, 12), HOUR, do_twa=True)
    # 10:00-10:30 holds 2 (from before the period), 10:30-11:00 holds 4; 11:00-11:45 holds 6, 11:45-12:00 holds 10.
    assert out["value"].tolist() == [3.0, 7.0]
    assert out["cnt"].tolist() == [1, 2]


def test_time_weighted_average_has_no_value_from_now_on() -> None:
    soon = pd.Timestamp.now(tz="UTC").ceil("h") + HOUR  # now lies in the first bin, or on the second's start
    gateway = _Gateway([(soon - pd.Timedelta(hours=30), 2.0), (soon + pd.Timedelta(minutes=10), 4.0)])
    out = _client(gateway).get_time_weighted_average("m", "a", "power", soon - 2 * HOUR, soon + HOUR, HOUR, default_if_none_value=-1)
    # The first bin is weighed up to now; the others start after now, the last though it holds a reading.
    assert out["value"].tolist() == [2.0, -1.0, -1.0]
    assert out["cnt"].tolist() == [0, 0, 1]


def test_time_weighted_average_before_the_first_reading_gets_the_default() -> None:
    gateway = _Gateway([(_t(16, 11, 30), 4.0)])
    out = _client(gateway).get_values_range("m", "a", "power", _t(16, 10), _t(16, 12), HOUR, do_twa=True, default_if_none_value=-1)
    assert out["value"].tolist() == [-1.0, 4.0]


def test_raw_values_are_the_numeric_readings_of_the_period() -> None:
    gateway = _Gateway([(_t(16, 9), 1.0), (_t(16, 10, 5), 1.5), (_t(16, 10, 6), "x"), (_t(16, 11, 59), 2), (_t(16, 12), 3.0)])
    raw = _client(gateway).get_values_range("m", "a", "power", _t(16, 10), _t(16, 12), HOUR, raw_values=True)
    assert raw["value"].tolist() == [1.5, 2.0]
    assert raw["cnt"].tolist() == [1, 1]
    assert list(raw.index) == [pd.Timestamp(_t(16, 10, 5)), pd.Timestamp(_t(16, 11, 59))]
    assert raw.index.name == "time"


def test_a_full_answer_is_read_again_in_halves() -> None:
    gateway = _Gateway([(_t(16, 10, m), float(m)) for m in range(10)], limit=4)
    start = datetime(2025, 11, 17, tzinfo=UTC)  # a Monday, 365 days before
    out = _client(gateway).get_values_range("m", "a", "power", start, _t(17), timedelta(days=7), do_average=True)
    assert out["cnt"].sum() == 10
    assert out.loc[pd.Timestamp("2026-11-16", tz="UTC"), "value"] == 4.5
    spans = gateway.spans()
    # The year answered 4 of its 10 rows, and was read again in halves down to minutes, then in
    # growing spans after the readings.
    assert spans[0] == ("2025-11-17T00:00:00.000000Z", "2026-11-16T23:59:59.999999Z")
    assert 20 < len(spans) < 60
    assert spans[-1][1] == "2026-11-16T23:59:59.999999Z"


def test_a_window_the_query_refuses_is_refused() -> None:
    client = _client(_Gateway([(_t(16, 10), 1.0)]))
    with pytest.raises(RuntimeError, match="not exceed 366 days"):
        client.get_values_range("m", "a", "power", datetime(2025, 11, 10, tzinfo=UTC), _t(17), timedelta(days=7), do_average=True)
    with pytest.raises(RuntimeError, match="run forward"):
        client.get_values_range("m", "a", "power", _t(17), _t(16), HOUR, raw_values=True)


def test_latest_value_needs_no_get_when_the_newest_reading_is_before() -> None:
    gateway = _Gateway([(_t(16, 9), 5.0), (_t(16, 11), 7.0), (_t(16, 12), None)])
    client = _client(gateway)
    assert client.get_latest_value("m", "a", "power", _t(16, 12, 30)) == 7.0  # the newest holds no number
    assert client.get_latest_value("m", "a", "power", _t(16, 10)) == 5.0
    assert client.get_latest_value("m", "a", "power", _t(16, 11)) == 7.0
    assert client.get_latest_value("m", "a", "power", _t(16, 12)) == 7.0
    assert client.get_latest_value("m", "a", "power", _t(16, 8), default_value_if_none=-1) == -1


def test_another_component_reporting_the_signal_is_refused() -> None:
    gateway = _Gateway([(_t(16, 9), 5.0), (_t(16, 9), 6.0, "telemetry", "pump")])
    with pytest.raises(ValueError, match="also reported by 'pump'.*every component together"):
        _client(gateway).get_values_range("m", "a", "power", _t(16, 10), _t(16, 12), HOUR, do_average=True)
    with pytest.raises(ValueError, match="also reported by the root"):
        _client(gateway, component_id="pump").get_latest_value("m", "a", "power", _t(16, 10))
    only_pump = _Gateway([(_t(16, 9), 6.0, "telemetry", "pump")])
    assert _client(only_pump, component_id="pump").get_latest_value("m", "a", "power", _t(16, 10)) == 6.0


def test_several_types_must_be_narrowed_as_on_the_query() -> None:
    gateway = _Gateway([(_t(16, 10), 1.0), (_t(16, 10), 2.0, "propertyReported")])
    client = _client(gateway, types=["telemetry", "propertyReported"])
    with pytest.raises(ValueError, match="several types"):
        client.get_values_range("m", "a", "power", _t(16, 10), _t(16, 12), HOUR, do_average=True)
    narrowed = _client(gateway, types=["propertyReported"])
    assert narrowed.get_values_range("m", "a", "power", _t(16, 10), _t(16, 11), HOUR, do_average=True)["value"].tolist() == [2.0]


def test_what_get_cannot_answer_the_same_is_refused() -> None:
    client = _client(_Gateway([(_t(16, 10), 1.0)]))
    with pytest.raises(NotImplementedError, match="dump_values needs timeseries/query"):
        client.dump_values("m", "a", _t(16, 10), _t(16, 12))
    forecasts = _client(_Gateway(), types=["Forecast"])
    with pytest.raises(NotImplementedError, match="read only the types telemetry, propertyReported"):
        forecasts.get_latest_value("m", "a", "power", _t(16, 10))


def test_an_asset_the_api_does_not_find_has_no_readings() -> None:
    gateway = _Gateway()
    answer = gateway._post_json

    def post(path: str, *, json: dict) -> dict:
        if path == "timeseries/get":
            _refuse(path, 404, "Asset not found")
        return answer(path, json=json)

    gateway._post_json = post
    out = _client(gateway).get_values_range("m", "a", "power", _t(16, 10), _t(16, 12), HOUR, do_average=True, default_if_none_value=3)
    assert out["value"].tolist() == [3.0, 3.0]


def test_a_gateway_without_get_says_so() -> None:
    gateway = _Gateway()

    def post(path: str, *, json: dict) -> dict:
        _refuse(path, 404)

    gateway._post_json = post
    with pytest.raises(RuntimeError, match="routes neither timeseries/query nor timeseries/get/last"):
        _client(gateway).get_latest_value("m", "a", "power", _t(16, 10))


def test_a_refused_get_says_why() -> None:
    gateway = _Gateway()
    answer = gateway._post_json

    def post(path: str, *, json: dict) -> dict:
        if path == "timeseries/get":
            _refuse(path, 400, "The search span must not exceed 30 days")
        return answer(path, json=json)

    gateway._post_json = post
    with pytest.raises(RuntimeError, match=r"refused the read of timeseries/get \(400\): The search span must not exceed 30 days"):
        _client(gateway).get_values_range("m", "a", "power", _t(16, 10), _t(16, 12), HOUR, do_average=True)
