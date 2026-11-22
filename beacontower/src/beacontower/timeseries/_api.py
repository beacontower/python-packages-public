"""Telemetry read through Beacon Tower's timeseries API (``POST timeseries/query``)."""

import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

import httpx
import pandas as pd

from beacontower.metadata.client import BeaconTowerClient

if TYPE_CHECKING:
    from beacontower.timeseries._api_get import GetReads

logger = logging.getLogger(__name__)

TimeWeightMethod = Literal["locf", "linear"]

_QUERY_PATH = "timeseries/query"
_ALL_TYPES = ("telemetry", "propertyReported", "propertyDesired", "Forecast", "Rule")
# The API's limits on a page (its defaults; a deployment may set others).
_MAX_PAGE_SIZE = 10_000
_MAX_BINS_PAGE_SPAN = timedelta(days=1000)
# The API cuts bins in whole multiples of the bin size from this instant, a Monday.
_GRID_ORIGIN = pd.Timestamp("2000-01-03", tz="UTC")
# Logged once each time a reader switches to timeseries/get.
_GET_FALLBACK_WARNING = (
    "The gateway does not route timeseries/query (it answered 404), so telemetry is read through timeseries/get "
    "instead, until do_reset. This fallback is temporary and will be removed once gateways route the query "
    "(gateway 0.7.0-prod.5 and later do). Its answers differ as the beacontower README lists under "
    '"Gateways without timeseries/query": readings of every model version are read, a signal that another '
    "component also reports is refused, only telemetry and propertyReported are read, and dump_values is refused."
)


def _iso(dt: datetime) -> str:
    """Write an instant as the API reads it; a time without a zone is UTC."""
    return (dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)).isoformat()


def _iso_duration(size: timedelta) -> str:
    """Write a bin size as an ISO 8601 duration in seconds, e.g. ``PT900S`` or ``PT0.5S``."""
    seconds, microseconds = divmod(size // timedelta(microseconds=1), 1_000_000)
    fraction = f".{microseconds:06d}".rstrip("0") if microseconds else ""
    return f"PT{seconds}{fraction}S"


def _times(values: Sequence[str]) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(pd.to_datetime(list(values), utc=True, format="ISO8601"))


class ApiTelemetry:
    """Reads telemetry through the timeseries API. Use it through ``TelemetryClient.from_api``."""

    def __init__(
        self,
        client: BeaconTowerClient,
        types: Sequence[str] = ("telemetry",),
        component_id: str | None = None,
        match_model: bool = True,
    ) -> None:
        """Create a reader on an authenticated Beacon Tower client.

        `types` are the timeseries types a read takes its values from and `component_id`
        the component, the root when omitted. With `match_model` a read keeps to the rows
        written under exactly the model id it is given, as the database read does.
        """
        self.client = client
        self.types = list(types)
        self.component_id = component_id
        self.match_model = match_model
        # Set once the gateway answers that it does not route timeseries/query.
        self._get_reads: GetReads | None = None

    def _read(self, model_id: str | None, timeseries_name: str | None, **members: Any) -> dict[str, Any]:
        read: dict[str, Any] = {"types": self.types, **members}
        if timeseries_name is not None:
            read["signals"] = [timeseries_name]
        if self.component_id is not None:
            read["componentId"] = self.component_id
        if self.match_model and model_id:
            read["modelVersion"] = model_id
        return read

    def _query(self, select: str, asset_id: str, read: dict[str, Any], page_size: int | None = None) -> list[dict[str, Any]]:
        """Post one query and follow its tokens; a series cut by a page is joined again.

        The API answers a query 200 or a problem, never 404: a 404 is the gateway's, which
        does not route ``timeseries/query``. The read is then made of ``timeseries/get``,
        and so is every read after it until :meth:`do_reset`; the switch is logged as a
        warning.
        """
        if self._get_reads is not None:
            return self._get_reads.query(select, asset_id, read)
        body: dict[str, Any] = {"select": select, "assetScope": [asset_id], "read": read}
        if page_size is not None:
            body["pageSize"] = page_size
        series: dict[tuple[Any, ...], dict[str, Any]] = {}
        while True:
            try:
                answer = self.client._post_json(_QUERY_PATH, json=body)  # noqa: SLF001
            except httpx.HTTPStatusError as error:
                if error.response.status_code == httpx.codes.NOT_FOUND and "continuationToken" not in body:
                    from beacontower.timeseries._api_get import GetReads  # noqa: PLC0415 -- only for gateways that need it

                    logger.warning(_GET_FALLBACK_WARNING)
                    self._get_reads = GetReads(self.client)
                    return self._get_reads.query(select, asset_id, read)
                detail = _problem_detail(error.response)
                raise RuntimeError(f"the timeseries API refused the query ({error.response.status_code}): {detail}") from error
            for entry in answer.get(select) or []:
                key = (entry.get("assetId"), entry.get("name"), entry.get("type"), entry.get("componentId"), entry.get("modelId"))
                if key in series:
                    series[key]["items"].extend(entry.get("items") or [])
                else:
                    series[key] = {**entry, "items": list(entry.get("items") or [])}
            token = answer.get("continuationToken")
            if not token:
                return list(series.values())
            body["continuationToken"] = token

    @staticmethod
    def _one_series(entries: list[dict[str, Any]], timeseries_name: str) -> list[dict[str, Any]]:
        """Return the items of the one series a read of one name and component answers."""
        measured = [entry for entry in entries if entry.get("type") is not None] or entries
        if len(measured) > 1:
            found = sorted({str(entry.get("type")) for entry in measured})
            raise ValueError(f"{timeseries_name} is stored under several types ({', '.join(found)}); pick one with `types`")
        return list(measured[0]["items"]) if measured else []

    def _bins(  # noqa: PLR0913, PLR0917
        self,
        model_id: str,
        asset_id: str,
        timeseries_name: str,
        dt_start: datetime,
        dt_end: datetime,
        bin_size: timedelta,
        **members: Any,
    ) -> pd.DataFrame:
        read = self._read(
            model_id, timeseries_name, **{"from": _iso(dt_start), "to": _iso(dt_end), "binSize": _iso_duration(bin_size)}, **members
        )
        page_size = max(1, min(_MAX_PAGE_SIZE, _MAX_BINS_PAGE_SPAN // bin_size))
        items = self._one_series(self._query("bins", asset_id, read, page_size), timeseries_name)
        res = pd.DataFrame(
            {"value": [item.get("value") for item in items], "cnt": [item.get("count", 0) for item in items]},
            index=_times([item["timestamp"] for item in items]),
        )
        res.index.name = "bin"
        res["value"] = res["value"].astype(float)
        res["cnt"] = res["cnt"].fillna(0).astype(int)
        return res

    def check_connection(self) -> None:
        """Verify the API is reachable and the credentials authenticate."""
        self.client.check_auth()

    def do_rollback(self) -> None:
        """Nothing to roll back: the API holds no session. Present to match the database reader."""

    def do_reset(self) -> None:
        """Forget the access token and what the gateway routes; the next read starts afresh."""
        self.client.header = None
        self._get_reads = None

    def get_latest_value(
        self, model_id: str, asset_id: str, timeseries_name: str, dt: datetime, default_value_if_none: float = 0
    ) -> float | int:
        """Return the latest value at or before `dt`, falling back to `default_value_if_none`."""
        entries = self._query("valueAt", asset_id, self._read(model_id, timeseries_name, at=_iso(dt)))
        items = self._one_series(entries, timeseries_name)
        if not items or items[0].get("value") is None:
            return default_value_if_none
        return float(items[0]["value"])

    def dump_values(self, model_id: str, asset_id: str, dt_start: datetime, dt_end: datetime) -> pd.DataFrame:
        """Dump all values for an asset over [dt_start, dt_end), one row per reading.

        `type` holds the timeseries type (``telemetry``, ``propertyReported`` and so on):
        the API does not name the value's kind, which the database reader puts there. A
        gateway that does not route ``timeseries/query`` cannot answer it.
        """
        read = self._read(model_id, None, **{"from": _iso(dt_start), "to": _iso(dt_end), "include": ["insertedAt"]})
        read["types"] = list(_ALL_TYPES)
        rows = [
            {
                "model_id": entry.get("modelId"),
                "asset_id": entry.get("assetId"),
                "timeseries_name": entry.get("name"),
                "time": item["timestamp"],
                "insertion_time": item.get("insertedAt"),
                "type": entry.get("type"),
                "value": item.get("value"),
            }
            for entry in self._query("rows", asset_id, read, _MAX_PAGE_SIZE)
            for item in entry["items"]
            if item.get("value") is not None
        ]
        res = pd.DataFrame(rows, columns=["model_id", "asset_id", "timeseries_name", "time", "insertion_time", "type", "value"])
        for column in ("time", "insertion_time"):
            res[column] = pd.to_datetime(res[column], utc=True, format="ISO8601")
        return res.sort_values(by="time", kind="stable").reset_index(drop=True)

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
        """Time-weighted average per bin over [dt_start, dt_end), computed by the API.

        The API holds each reading until the next (``how="locf"``); it has no ``"linear"``,
        which only the database reader offers. A deployment whose API does not have the
        function ``twa`` yet refuses the read.
        """
        if how != "locf":
            raise NotImplementedError(f"the timeseries API computes only how='locf', not {how!r}; use the database reader for it")
        start = pd.Timestamp(dt_start)
        start = start.tz_localize("UTC") if start.tzinfo is None else start.tz_convert("UTC")
        if (start - _GRID_ORIGIN) % bin_size:
            raise ValueError(
                f"the timeseries API cuts {bin_size} bins on a fixed grid, counted from a Monday at midnight UTC, which"
                f" {dt_start} is not on; start on the grid, or use the database reader, which counts bins from dt_start"
            )
        # Whole bins, as the database reader: a last bin past dt_end is read to its own end.
        dt_end = max(dt_end, dt_start + -(-(dt_end - dt_start) // bin_size) * bin_size)
        res = self._bins(
            model_id, asset_id, timeseries_name, dt_start, dt_end, bin_size, function="twa", fill="value", default=default_if_none_value
        )
        return res if return_count else res["value"]

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
        """Fetch binned telemetry for one (model, asset, timeseries) over a time range.

        The same reads as the database reader's, in the same priority (twa > average >
        raw > value-at-time), each asked of the API. The API cuts bins on its own fixed
        grid (whole multiples of `bin_size` from a Monday at midnight UTC), so give a
        `dt_start` on that grid to get the database reader's bins.
        """
        if do_twa:
            return self.get_time_weighted_average(
                model_id, asset_id, timeseries_name, dt_start, dt_end, bin_size, "locf", return_count, default_if_none_value
            )
        if do_average:
            res = self._bins(
                model_id, asset_id, timeseries_name, dt_start, dt_end, bin_size, function="avg", fill="value", default=default_if_none_value
            )
        elif raw_values:
            read = self._read(model_id, timeseries_name, **{"from": _iso(dt_start), "to": _iso(dt_end)})
            items = [
                item
                for item in self._one_series(self._query("rows", asset_id, read, _MAX_PAGE_SIZE), timeseries_name)
                if isinstance(item.get("value"), int | float) and not isinstance(item.get("value"), bool)
            ]
            res = pd.DataFrame({"value": [float(item["value"]) for item in items], "cnt": 1}, index=_times([i["timestamp"] for i in items]))
            res.index.name = "time"
        else:
            # The reading as of each label: the last reading of the bin that ends at the label.
            res = self._bins(
                model_id,
                asset_id,
                timeseries_name,
                dt_start - bin_size,
                dt_end,
                bin_size,
                function="last",
                fill="previous",
                default=default_if_none_value,
            )
            res.index = res.index + bin_size
        return res if return_count else res["value"]


def _problem_detail(response: httpx.Response) -> str:
    """Return the `detail` of the API's problem document, or the body when it is not one."""
    try:
        return str(response.json().get("detail") or response.text)
    except ValueError:
        return response.text
