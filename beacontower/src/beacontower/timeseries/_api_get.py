"""The API reader's reads made of ``timeseries/get``, for gateways without ``timeseries/query``.

Gateways released before ``timeseries/query`` was routed answer it 404, and route only
``timeseries/get`` (a signal's readings in a span) and ``timeseries/get/last`` (the newest
reading of each component). :class:`GetReads` reads those and bins the readings in the
client as ``timeseries/query`` bins them in the server, and answers the entries the query
answers, so that the reader makes the same frames of them.

What it cannot do the same, it refuses: a signal that a component other than the one
read also reports (``timeseries/get`` reads every component together), types other than
``telemetry`` and ``propertyReported`` (``timeseries/get/last`` has no others), and reads
of every signal of an asset (``timeseries/get`` needs the names, and answers no insertion
time). It does not keep to the read's model version: ``timeseries/get`` has no filter for it.
"""

import dataclasses
import math
from collections.abc import Iterator
from typing import Any

import httpx
import pandas as pd

from beacontower.metadata.client import BeaconTowerClient
from beacontower.timeseries._api import _GRID_ORIGIN, _problem_detail

_GET_PATH = "timeseries/get"
_LAST_PATH = "timeseries/get/last"
# The types timeseries/get/last tells apart: it reads any other as propertyReported.
_GET_TYPES = ("telemetry", "propertyReported")
# The longest span timeseries/get reads at once, and the longest window timeseries/query
# reads (the API's defaults; a deployment may set others).
_MAX_SPAN = pd.Timedelta(days=366)
_MAX_WINDOW = pd.Timedelta(days=366)
# The first span read back for the reading before a period; each empty one is followed
# by one 8 times as long.
_FIRST_LOOK_BACK = pd.Timedelta(days=1)
# How far back the reading before a period is looked for: ten years. timeseries/query looks
# without end, which timeseries/get can only do a year at a time.
_LOOK_BACK = pd.Timedelta(days=3653)
# The database keeps whole microseconds; timeseries/get reads up to and including searchSpanTo.
_TICK = pd.Timedelta(microseconds=1)
# The (function, fill) pairs the reader asks for.
_BIN_READS = {("avg", "value"), ("last", "previous"), ("twa", "value")}

_NOT_ROUTED = "this gateway does not route timeseries/query, and timeseries/get"

Reading = tuple[pd.Timestamp, float]
Items = list[dict[str, Any]]


@dataclasses.dataclass(frozen=True)
class _Signal:
    """One signal of one asset, read in some types."""

    asset_id: str
    name: str
    types: tuple[str, ...]


def _instant(text: str) -> pd.Timestamp:
    """Read an instant the API wrote; one without a zone is UTC."""
    time = pd.Timestamp(text)
    return time.tz_localize("UTC") if time.tzinfo is None else time.tz_convert("UTC")


def _text(time: pd.Timestamp) -> str:
    """Write an instant for timeseries/get, in UTC to the microsecond."""
    return str(time.strftime("%Y-%m-%dT%H:%M:%S.%fZ"))


def _number(value: Any) -> float | None:
    """Return a reading's value when it is a finite number, as timeseries/query reads it."""
    if isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


def _numeric(entries: Items) -> dict[str, list[Reading]]:
    """Return the readings of timeseries/get's entries that hold a number, per type, by time."""
    readings: dict[str, list[Reading]] = {}
    for entry in entries:
        for item in entry.get("items") or []:
            value = _number((item or {}).get("value"))
            if value is not None:
                readings.setdefault(str(entry.get("type")), []).append((_instant(item["timestamp"]), value))
    for series in readings.values():
        series.sort(key=lambda reading: reading[0])
    return readings


class _Grid:
    """The bins of [start, end) on the API's grid.

    The first bin holds `start` and the last the instant before `end`; either may be
    partial.
    """

    def __init__(self, start: pd.Timestamp, end: pd.Timestamp, width: pd.Timedelta) -> None:
        self.start, self.end, self.width = start, end, width
        self.first = _GRID_ORIGIN + ((start - _GRID_ORIGIN) // width) * width
        last = _GRID_ORIGIN + ((end - _TICK - _GRID_ORIGIN) // width) * width
        self.count = max(0, (last - self.first) // width + 1)

    def bin_start(self, cell: int) -> pd.Timestamp:
        """Return where bin `cell` starts, which labels it."""
        return self.first + cell * self.width

    def cells(self, readings: list[Reading]) -> list[list[Reading]]:
        """Put readings of [start, end), in time order, into their bins."""
        binned: list[list[Reading]] = [[] for _ in range(self.count)]
        for reading in readings:
            binned[(reading[0] - self.first) // self.width].append(reading)
        return binned


def _average(grid: _Grid, readings: list[Reading], default: float) -> Items:
    """Bin as function ``avg`` with fill ``value``: each bin's mean, else the default."""
    return [
        {
            "timestamp": grid.bin_start(cell).isoformat(),
            "value": sum(value for _, value in here) / len(here) if here else default,
            "count": len(here),
        }
        for cell, here in enumerate(grid.cells(readings))
    ]


def _last(grid: _Grid, readings: list[Reading], seed: float | None, default: float) -> Items:
    """Bin as function ``last`` with fill ``previous``: each bin's last, else the one before."""
    items = []
    held = default if seed is None else seed
    for cell, here in enumerate(grid.cells(readings)):
        held = here[-1][1] if here else held
        items.append({"timestamp": grid.bin_start(cell).isoformat(), "value": held, "count": len(here)})
    return items


def _time_weighted(grid: _Grid, readings: list[Reading], seed: float | None, default: float, now: pd.Timestamp) -> Items:
    """Bin as function ``twa`` with fill ``value``: each reading weighed by the seconds it held.

    A reading holds until the next, the bin's end, the period's end or now, whichever comes
    first; the reading before the bin holds from its start to its first reading. A bin that
    starts at or after now, or has nothing to weigh, gets the default.
    """
    items = []
    state = seed
    for cell, here in enumerate(grid.cells(readings)):
        start = grid.bin_start(cell)
        end = min(start + grid.width, grid.end)
        before, state = state, (here[-1][1] if here else state)
        value = None
        span_from = max(start, grid.start)
        if span_from < now:
            total = seconds = 0.0
            for i, (time, reading) in enumerate(here):
                until = min(end, now, *([here[i + 1][0]] if i + 1 < len(here) else []))
                held = max(0.0, (until - time).total_seconds())
                total, seconds = total + reading * held, seconds + held
            if before is not None:
                lead = max(0.0, (min(end, now) - span_from).total_seconds() - seconds)
                total, seconds = total + before * lead, seconds + lead
            value = total / seconds if seconds > 0 else None
        items.append({"timestamp": start.isoformat(), "value": default if value is None else value, "count": len(here)})
    return items


class GetReads:
    """Answers the API reader's ``timeseries/query`` reads of one signal with ``timeseries/get``."""

    def __init__(self, client: BeaconTowerClient) -> None:
        """Read through `client`'s gateway."""
        self.client = client

    def query(self, select: str, asset_id: str, read: dict[str, Any]) -> Items:
        """Answer a ``timeseries/query`` of one asset as the query answers it: its entries.

        `select` is ``bins``, ``valueAt`` or ``rows`` and `read` the query's read, of one
        signal. Raises ``NotImplementedError`` for a read the gateway's routes cannot make the
        same, and ``ValueError`` when another component reports the signal too.
        """
        signals = read.get("signals") or []
        if len(signals) != 1:
            raise NotImplementedError(f"{_NOT_ROUTED} reads named signals only; dump_values needs timeseries/query")
        signal = _Signal(asset_id, signals[0], tuple(read["types"]))
        if set(signal.types) - set(_GET_TYPES):
            raise NotImplementedError(f"{_NOT_ROUTED}/last read only the types {', '.join(_GET_TYPES)}, not {', '.join(signal.types)}")
        component = read.get("componentId")
        entry = {"assetId": asset_id, "name": signal.name, "componentId": component}
        newest = self._newest(signal, component)
        if select == "valueAt":
            seeds = self._seeds(signal, newest, _instant(read["at"]) + _TICK)
            found = {type_: [{"timestamp": time.isoformat(), "value": value}] for type_, (time, value) in seeds.items()}
            return _entries(entry, signal, found, [])
        start, end = _instant(read["from"]), _instant(read["to"])
        if end < start or end - start > _MAX_WINDOW:  # refused as timeseries/query refuses it
            raise RuntimeError(f"the read's window must run forward and not exceed {_MAX_WINDOW.days} days, as timeseries/query reads it")
        readings = self._readings(signal, start, end)
        if select == "rows":
            found = {type_: [{"timestamp": time.isoformat(), "value": value} for time, value in rows] for type_, rows in readings.items()}
            return _entries(entry, signal, found, [])
        if select != "bins" or (read.get("function"), read.get("fill")) not in _BIN_READS:
            raise NotImplementedError(f"{_NOT_ROUTED} cannot answer {select} with function {read.get('function')!r}")
        return self._bins(signal, entry, read, newest, readings)

    def _bins(
        self,
        signal: _Signal,
        entry: dict[str, Any],
        read: dict[str, Any],
        newest: dict[str, tuple[pd.Timestamp, Any]],
        readings: dict[str, list[Reading]],
    ) -> Items:
        """Bin the readings of the period as the query's `read` asks."""
        start, end, function = _instant(read["from"]), _instant(read["to"]), read["function"]
        grid, default = _Grid(start, end, pd.Timedelta(read["binSize"])), float(read["default"])
        # Under fill previous and for twa the reading before the period holds until the first
        # one in it, and makes a series on its own. A type with a reading in its first bin (last)
        # or on the period's start (twa) needs none.
        before = {}
        if function != "avg":
            settled = grid.first + grid.width if function == "last" else start + _TICK
            wanted = {type_: row for type_, row in newest.items() if not (type_ in readings and readings[type_][0][0] < settled)}
            before = {type_: value for type_, (_, value) in self._seeds(signal, wanted, start).items()}
        now = pd.Timestamp.now(tz="UTC")

        def items(type_: str | None) -> Items:
            series, seed = readings.get(type_ or "", []), before.get(type_ or "")
            if function == "avg":
                return _average(grid, series, default)
            if function == "last":
                return _last(grid, series, seed, default)
            return _time_weighted(grid, series, seed, default, now)

        series = {type_: items(type_) for type_ in signal.types if type_ in readings or type_ in before}
        return _entries(entry, signal, series, [] if series else items(None))

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any] | None:
        """Post to the API; return None when it answers that there is no such asset to read."""
        try:
            answer: dict[str, Any] = self.client._post_json(path, json=body)  # noqa: SLF001
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            if status == httpx.codes.NOT_FOUND and not error.response.text:
                raise RuntimeError(f"this gateway routes neither timeseries/query nor {path}") from error
            if status == httpx.codes.NOT_FOUND:  # the API's own: an asset that is not there, or not the caller's to view
                return None
            raise RuntimeError(f"the timeseries API refused the read of {path} ({status}): {_problem_detail(error.response)}") from error
        return answer

    def _newest(self, signal: _Signal, component: str | None) -> dict[str, tuple[pd.Timestamp, Any]]:
        """Return the signal's newest row per type, once sure that no other component reports it.

        ``timeseries/get`` reads every component of the asset together, so a read keeps to
        one only when the signal has no other.
        """
        body = {"items": [{"assetId": signal.asset_id, "timeseries": [{"name": signal.name, "type": type_} for type_ in signal.types]}]}
        rows = [
            row
            for row in (self._post(_LAST_PATH, body) or {}).get("items") or []
            if row.get("assetId", signal.asset_id) == signal.asset_id
            and row.get("timeseriesName") == signal.name
            and row.get("timeseriesType") in signal.types
        ]
        others = sorted({row.get("componentId") or "" for row in rows} - {component or ""})
        if others:
            components = ", ".join(repr(other) if other else "the root" for other in others)
            raise ValueError(
                f"{signal.name} of {signal.asset_id} is also reported by {components}; {_NOT_ROUTED} reads every component"
                " together. Read through a gateway that routes timeseries/query, or from the database"
            )
        return {row["timeseriesType"]: (_instant(row["timestamp"]), row.get("value")) for row in rows}

    def _get(self, signal: _Signal, start: pd.Timestamp, end: pd.Timestamp) -> tuple[Items, bool]:
        """Post one timeseries/get of [start, end); also say whether it stopped at its row limit."""
        body = {
            "assetId": signal.asset_id,
            "timeseriesName": [signal.name],
            "types": list(signal.types),
            "searchSpanFrom": _text(start),
            "searchSpanTo": _text(end - _TICK),
        }
        entries = (self._post(_GET_PATH, body) or {}).get("items") or []
        return entries, any(entry.get("queryResultLimitReached") for entry in entries)

    def _windows(self, signal: _Signal, start: pd.Timestamp, end: pd.Timestamp, newest_first: bool = False) -> Iterator[Items]:
        """Yield timeseries/get's entries of [start, end) span by span, oldest or newest first.

        A span is the longest the API reads, or shorter where it holds more rows than one
        answer does (which then holds some of them, in no order): that span is read again
        in halves, and the spans after it are as short until one holds less than half an
        answer. Newest first, the spans start short and grow while they hold nothing.
        """
        span, answer = (_FIRST_LOOK_BACK if newest_first else _MAX_SPAN), None
        while start < end:
            lo, hi = (max(start, end - span), end) if newest_first else (start, min(end, start + span))
            entries, cut = self._get(signal, lo, hi)
            rows = sum(len(entry.get("items") or []) for entry in entries)
            if cut and hi - lo > _TICK:
                span, answer = max(_TICK, ((hi - lo) / 2).floor("us")), rows
                continue
            yield entries
            start, end = (start, lo) if newest_first else (hi, end)
            if newest_first and not _numeric(entries):
                span = min(_MAX_SPAN, span * 8)
            elif answer is not None and rows < answer / 2:
                span = min(_MAX_SPAN, span * 2)

    def _readings(self, signal: _Signal, start: pd.Timestamp, end: pd.Timestamp) -> dict[str, list[Reading]]:
        """Return the readings of [start, end) that hold a number, per type, in time order."""
        readings: dict[str, list[Reading]] = {}
        for entries in self._windows(signal, start, end):
            for type_, series in _numeric(entries).items():
                readings.setdefault(type_, []).extend(series)
        return readings

    def _seeds(self, signal: _Signal, newest: dict[str, tuple[pd.Timestamp, Any]], before: pd.Timestamp) -> dict[str, Reading]:
        """Return the newest reading before `before` holding a number, per type, ten years back."""
        seeds = {}
        for type_, (time, value) in newest.items():
            if time < before and _number(value) is not None:
                seeds[type_] = (time, float(value))
                continue
            # Nothing came after the newest row, so the walk back starts at it or at `before`.
            one_type = dataclasses.replace(signal, types=(type_,))
            for entries in self._windows(one_type, before - _LOOK_BACK, min(before, time + _TICK), newest_first=True):
                found = _numeric(entries).get(type_)
                if found:
                    seeds[type_] = found[-1]
                    break
        return seeds


def _entries(entry: dict[str, Any], signal: _Signal, found: dict[str, Items], no_series: Items) -> Items:
    """Return the query's entries: one per type that has a series, else one with no type."""
    return [{**entry, "type": type_, "items": found[type_]} for type_ in signal.types if type_ in found] or [
        {**entry, "type": None, "items": no_series}
    ]
