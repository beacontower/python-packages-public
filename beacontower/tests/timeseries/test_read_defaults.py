"""TelemetryClient's own checks, made before either source is asked."""

import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from fractions import Fraction

import numpy as np
import pytest
from beacontower.timeseries import TelemetryClient

START = datetime(2026, 11, 16, tzinfo=UTC)
END = START + timedelta(hours=2)


class _Source:
    """Stands in for either reader and records the default each read is given."""

    def __init__(self) -> None:
        self.defaults: list[object] = []

    def get_latest_value(self, *args: object) -> float:
        self.defaults.append(args[-1])
        return 1.0

    def get_values_range(self, *args: object) -> None:
        self.defaults.append(args[-1])

    def get_time_weighted_average(self, *args: object) -> None:
        self.defaults.append(args[-1])


READS = {
    "get_latest_value": lambda c, d: c.get_latest_value("m", "a", "power", END, default_value_if_none=d),
    "get_values_range": lambda c, d: c.get_values_range("m", "a", "power", START, END, timedelta(hours=1), default_if_none_value=d),
    "get_time_weighted_average": lambda c, d: c.get_time_weighted_average(
        "m", "a", "power", START, END, timedelta(hours=1), default_if_none_value=d
    ),
}


@pytest.mark.parametrize("read", READS)
@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf, np.float64("nan")])
def test_a_default_that_is_not_finite_raises_value_error(read: str, bad: object) -> None:
    source = _Source()
    with pytest.raises(ValueError, match="must be a finite number"):
        READS[read](TelemetryClient(source=source), bad)
    assert source.defaults == []


@pytest.mark.parametrize("read", READS)
@pytest.mark.parametrize("bad", ["0", None, True, False, Decimal(1), [0]])
def test_a_default_that_is_not_a_number_raises_type_error(read: str, bad: object) -> None:
    source = _Source()
    with pytest.raises(TypeError, match="must be a number"):
        READS[read](TelemetryClient(source=source), bad)
    assert source.defaults == []


@pytest.mark.parametrize("read", READS)
@pytest.mark.parametrize(
    ("good", "passed"),
    [(0, 0), (-2, -2), (1.5, 1.5), (np.float64(3.0), 3.0), (np.int64(4), 4), (Fraction(1, 2), 0.5)],
)
def test_a_finite_number_reaches_the_source_as_an_int_or_a_float(read: str, good: object, passed: float) -> None:
    source = _Source()
    READS[read](TelemetryClient(source=source), good)
    assert source.defaults == [passed]
    assert type(source.defaults[0]) is type(passed)
