"""Beacon Tower's API provider: which sources an environment polls, and their instances.

The provider fetches data no device measures (weather at a position, day-ahead electricity
prices, exchange rates) from external APIs into the platform. :class:`ApiProvidersClient` shows
which of its sources an environment has switched on, lists the instances with their settings and
state, and makes and removes them. Each source is a class: :class:`OpenMeteo`, :class:`Entsoe`
and :class:`Riksbank`.
"""

import logging

from beacontower.apiproviders.client import (
    PROVIDER_ID,
    STATUS_DEVICE_ID,
    ApiProvidersClient,
    ApiProvidersError,
    InstanceConfigError,
    InstanceExistsError,
    InstanceNotFoundError,
    SourceNotAvailableError,
)
from beacontower.apiproviders.datamodel import History, Instance, LastPoll, ProviderStatus, SourceState, SourceStatus
from beacontower.apiproviders.sources import Entsoe, OpenMeteo, OtherSource, Riksbank, Source, source_class, source_classes

__all__ = [
    "PROVIDER_ID",
    "STATUS_DEVICE_ID",
    "ApiProvidersClient",
    "ApiProvidersError",
    "Entsoe",
    "History",
    "Instance",
    "InstanceConfigError",
    "InstanceExistsError",
    "InstanceNotFoundError",
    "LastPoll",
    "OpenMeteo",
    "OtherSource",
    "ProviderStatus",
    "Riksbank",
    "Source",
    "SourceNotAvailableError",
    "SourceState",
    "SourceStatus",
    "source_class",
    "source_classes",
]

# For details see https://docs.python.org/3/howto/logging.html#configuring-logging-for-a-library
logging.getLogger(__name__).addHandler(logging.NullHandler())
