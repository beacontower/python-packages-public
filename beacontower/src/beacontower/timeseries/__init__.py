"""Timeseries related clients."""

import logging

from beacontower.timeseries.client import TelemetryClient
from beacontower.timeseries.datamodel import TimeseriesCredentials
from beacontower.timeseries.protocol import Telemetry, TelemetryUnavailable

__all__ = ["Telemetry", "TelemetryClient", "TelemetryUnavailable", "TimeseriesCredentials"]

# For details see https://docs.python.org/3/howto/logging.html#configuring-logging-for-a-library
logging.getLogger(__name__).addHandler(logging.NullHandler())
