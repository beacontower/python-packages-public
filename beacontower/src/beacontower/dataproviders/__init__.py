"""Beacon Tower's data provider: which sources an environment polls, and their instances.

The provider fetches data no device measures (weather at a position, day-ahead electricity
prices, exchange rates) from external APIs into the platform. :class:`DataProvidersClient` shows
which of its sources an environment has switched on, lists the instances with their settings and
state, and makes and removes them. Each source is a class: :class:`OpenMeteo`, :class:`Entsoe`
and :class:`Riksbank`.
"""

import logging

from beacontower.dataproviders.client import (
    PROVIDER_ID,
    STATUS_DEVICE_ID,
    DataProvidersClient,
    DataProvidersError,
    InstanceConfigError,
    InstanceExistsError,
    InstanceNotFoundError,
    SourceNotAvailableError,
)
from beacontower.dataproviders.datamodel import History, Instance, LastPoll, ProviderStatus, SourceState, SourceStatus
from beacontower.dataproviders.sources import Entsoe, OpenMeteo, OtherSource, Riksbank, Source, source_class, source_classes

__all__ = [
    "PROVIDER_ID",
    "STATUS_DEVICE_ID",
    "DataProvidersClient",
    "DataProvidersError",
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
