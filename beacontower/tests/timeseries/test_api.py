"""The API reader: the request each read sends, and the frame it makes of the answer."""

from datetime import UTC, datetime, timedelta

import httpx
import pandas as pd
import pytest
from beacontower.timeseries import TelemetryClient
from beacontower.timeseries._api import _iso_duration

HOUR = timedelta(hours=1)


def _t(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, 16, hour, minute, tzinfo=UTC)


class _Api:
    """Stands in for BeaconTowerClient: records the bodies posted and plays back answers."""

    def __init__(self, *answers: dict) -> None:
        self.answers = list(answers)
        self.bodies: list[dict] = []
        self.header = {"Authorization": "Bearer t"}

    def _post_json(self, path: str, *, json: dict) -> dict:
        assert path == "timeseries/query"
        self.bodies.append({**json, "read": dict(json["read"])})
        return self.answers.pop(0)


def _client(*answers: dict, **options) -> tuple[TelemetryClient, _Api]:
    api = _Api(*answers)
    return TelemetryClient.from_beacon_tower_client(api, **options), api


def _series(items: list[dict], type_: str | None = "telemetry", **more) -> dict:
    return {"assetId": "a", "name": "power", "type": type_, "componentId": None, "items": items, **more}


def test_bin_size_as_iso_duration() -> None:
    assert _iso_duration(timedelta(minutes=15)) == "PT900S"
    assert _iso_duration(timedelta(days=7)) == "PT604800S"
    assert _iso_duration(timedelta(milliseconds=500)) == "PT0.5S"


def test_average_asks_for_avg_with_a_default_and_returns_bins() -> None:
    client, api = _client(
        {
            "bins": [
                _series(
                    [
                        {"timestamp": "2026-11-16T10:00:00Z", "value": 2.0, "count": 3},
                        {"timestamp": "2026-11-16T11:00:00Z", "value": 7, "count": 0},
                    ]
                )
            ]
        }
    )
    out = client.get_values_range("dtmi:x:meter;1", "a", "power", _t(10), _t(12), HOUR, do_average=True, default_if_none_value=7)
    body = api.bodies[0]
    assert body["select"] == "bins"
    assert body["assetScope"] == ["a"]
    assert body["read"] == {
        "types": ["telemetry"],
        "signals": ["power"],
        "modelVersion": "dtmi:x:meter;1",
        "from": "2026-11-16T10:00:00+00:00",
        "to": "2026-11-16T12:00:00+00:00",
        "binSize": "PT3600S",
        "function": "avg",
        "fill": "value",
        "default": 7,
    }
    assert out["value"].tolist() == [2.0, 7.0]
    assert out["cnt"].tolist() == [3, 0]
    assert list(out.index) == [pd.Timestamp(_t(10)), pd.Timestamp(_t(11))]
    assert out.index.name == "bin"


def test_value_at_time_reads_the_bin_before_each_label() -> None:
    client, api = _client(
        {
            "bins": [
                _series(
                    [
                        {"timestamp": "2026-11-16T09:00:00Z", "value": 1.0, "count": 0},
                        {"timestamp": "2026-11-16T10:00:00Z", "value": 4.0, "count": 2},
                    ]
                )
            ]
        }
    )
    out = client.get_values_range("m", "a", "power", _t(10), _t(11), HOUR, return_count=False)
    read = api.bodies[0]["read"]
    assert (read["function"], read["fill"]) == ("last", "previous")
    assert read["from"] == "2026-11-16T09:00:00+00:00"
    assert read["to"] == "2026-11-16T11:00:00+00:00"
    assert list(out.index) == [pd.Timestamp(_t(10)), pd.Timestamp(_t(11))]
    assert out.tolist() == [1.0, 4.0]


def test_pages_are_followed_and_a_cut_series_joined() -> None:
    first = {"bins": [_series([{"timestamp": "2026-11-16T10:00:00Z", "value": 1.0, "count": 1}])], "continuationToken": "next"}
    second = {"bins": [_series([{"timestamp": "2026-11-16T11:00:00Z", "value": 2.0, "count": 1}])], "continuationToken": None}
    client, api = _client(first, second)
    out = client.get_values_range("m", "a", "power", _t(10), _t(12), HOUR, do_average=True)
    assert out["value"].tolist() == [1.0, 2.0]
    assert "continuationToken" not in api.bodies[0]
    assert api.bodies[1]["continuationToken"] == "next"


def test_page_size_keeps_within_the_days_a_page_may_cover() -> None:
    client, api = _client({"bins": []}, {"bins": []})
    client.get_values_range("m", "a", "power", _t(10), _t(12), timedelta(days=7), do_average=True)
    client.get_values_range("m", "a", "power", _t(10), _t(12), timedelta(seconds=1), do_average=True)
    assert [body["pageSize"] for body in api.bodies] == [142, 10_000]


def test_latest_value_and_its_default() -> None:
    client, api = _client({"valueAt": [_series([{"timestamp": "2026-11-16T09:59:00Z", "value": 12.5}])]}, {"valueAt": [_series([], None)]})
    assert client.get_latest_value("m", "a", "power", _t(10)) == 12.5
    assert api.bodies[0]["read"]["at"] == "2026-11-16T10:00:00+00:00"
    assert client.get_latest_value("m", "a", "power", _t(10), default_value_if_none=-1) == -1


def test_time_weighted_average_is_twa_and_linear_is_refused() -> None:
    client, api = _client({"bins": [_series([{"timestamp": "2026-11-16T10:00:00Z", "value": 17.5, "count": 2}])]})
    out = client.get_values_range("m", "a", "power", _t(10), _t(11), HOUR, do_twa=True)
    assert api.bodies[0]["read"]["function"] == "twa"
    assert out["value"].tolist() == [17.5]
    with pytest.raises(NotImplementedError, match="locf"):
        client.get_time_weighted_average("m", "a", "power", _t(10), _t(11), HOUR, how="linear")


def test_raw_values_and_dump() -> None:
    rows = {"rows": [_series([{"timestamp": "2026-11-16T10:05:00Z", "value": 1.5, "insertedAt": "2026-11-16T10:05:01Z"}], modelId="m")]}
    client, api = _client(rows, rows)
    raw = client.get_values_range("m", "a", "power", _t(10), _t(12), HOUR, raw_values=True)
    assert raw["value"].tolist() == [1.5]
    assert raw["cnt"].tolist() == [1]
    dump = client.dump_values("m", "a", _t(10), _t(12))
    assert "signals" not in api.bodies[1]["read"]
    assert api.bodies[1]["read"]["include"] == ["insertedAt"]
    assert len(api.bodies[1]["read"]["types"]) == 5
    assert dump.iloc[0].to_dict() == {
        "model_id": "m",
        "asset_id": "a",
        "timeseries_name": "power",
        "time": pd.Timestamp(_t(10, 5)),
        "insertion_time": pd.Timestamp("2026-11-16T10:05:01Z"),
        "type": "telemetry",
        "value": 1.5,
    }


def test_options_shape_the_read() -> None:
    client, api = _client({"bins": []}, types=["propertyReported"], component_id="c1", match_model=False)
    out = client.get_values_range("m", "a", "power", _t(10), _t(12), HOUR, do_average=True)
    read = api.bodies[0]["read"]
    assert read["types"] == ["propertyReported"]
    assert read["componentId"] == "c1"
    assert "modelVersion" not in read
    assert out.empty


def test_several_types_for_one_name_must_be_narrowed() -> None:
    item = [{"timestamp": "2026-11-16T10:00:00Z", "value": 1.0, "count": 1}]
    client, _ = _client({"bins": [_series(item), _series(item, "propertyReported")]}, types=["telemetry", "propertyReported"])
    with pytest.raises(ValueError, match="several types"):
        client.get_values_range("m", "a", "power", _t(10), _t(12), HOUR, do_average=True)


def test_a_refused_query_says_why() -> None:
    client, api = _client()

    def refuse(path: str, *, json: dict) -> dict:
        request = httpx.Request("POST", "http://gateway/timeseries/query")
        response = httpx.Response(400, json={"status": 400, "detail": "read.function is not a known function"}, request=request)
        raise httpx.HTTPStatusError("bad", request=request, response=response)

    api._post_json = refuse
    with pytest.raises(RuntimeError, match="read.function is not a known function"):
        client.get_values_range("m", "a", "power", _t(10), _t(12), HOUR, do_twa=True)


def test_time_weighted_average_reads_whole_bins() -> None:
    client, api = _client({"bins": [_series([{"timestamp": "2026-11-16T00:00:00Z", "value": 1.0, "count": 2}])]})
    client.get_time_weighted_average("m", "a", "power", _t(0), _t(5, 30), HOUR)
    assert api.bodies[0]["read"]["to"] == "2026-11-16T06:00:00+00:00"


def test_time_weighted_average_off_the_grid_is_refused() -> None:
    client, api = _client()
    with pytest.raises(ValueError, match="fixed grid"):
        client.get_time_weighted_average("m", "a", "power", _t(0, 20), _t(5, 20), HOUR)
    with pytest.raises(ValueError, match="fixed grid"):  # 2026-11-17 is a Tuesday: a week's bins start on Monday
        client.get_time_weighted_average(
            "m", "a", "power", datetime(2026, 11, 17, tzinfo=UTC), datetime(2026, 12, 1, tzinfo=UTC), timedelta(days=7)
        )
    assert api.bodies == []
