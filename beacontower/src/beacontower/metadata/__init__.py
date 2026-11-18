"""Metadata related clients."""

import logging

from beacontower.metadata.client import BeaconTowerClient
from beacontower.metadata.pagination import PaginatedList

__all__ = ["BeaconTowerClient", "PaginatedList"]

# For details see https://docs.python.org/3/howto/logging.html#configuring-logging-for-a-library
logging.getLogger(__name__).addHandler(logging.NullHandler())
