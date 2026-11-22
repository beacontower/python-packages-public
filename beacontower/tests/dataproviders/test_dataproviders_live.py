"""The data provider client against a real Beacon Tower; skipped unless BEACONTOWER_URL is set.

It reads the provider's status and instances, then makes one ENTSO-E instance (ENTSO-E's API costs
nothing), waits for its first poll, reads the prices through the timeseries API, and removes the
instance with its asset. Set:

* BEACONTOWER_URL: the API's address, ``https://<host>/api/v1``;
* BEACONTOWER_TOKEN, or BEACONTOWER_USER and BEACONTOWER_PASSWORD;
* BEACONTOWER_VERIFY=false for a deployment with a self-signed certificate;
* BEACONTOWER_ORGANIZATION when the login sees more than one organization;
* BEACONTOWER_BIDDING_ZONE to use another zone than FI.
"""

import os
import time
from datetime import UTC, datetime, timedelta

import pytest

from beacontower.dataproviders import DataProvidersClient, Entsoe, InstanceNotFoundError, SourceState
from beacontower.metadata.client import BeaconTowerClient

pytestmark = pytest.mark.skipif(not os.environ.get("BEACONTOWER_URL"), reason="BEACONTOWER_URL is not set")


@pytest.fixture(scope="module")
def providers() -> DataProvidersClient:
    verify = os.environ.get("BEACONTOWER_VERIFY", "true").lower() not in {"false", "0", "no"}
    bt = BeaconTowerClient(
        os.environ["BEACONTOWER_URL"],
        os.environ.get("BEACONTOWER_USER", ""),
        os.environ.get("BEACONTOWER_PASSWORD", ""),
        resolve_url=False,
        verify=verify,
    )
    if token := os.environ.get("BEACONTOWER_TOKEN"):
        bt.header = {"Authorization": f"Bearer {token}"}
    return DataProvidersClient(bt)


def test_status(providers: DataProvidersClient) -> None:
    status = providers.status()
    print(f"\nprovider {status.version}, reported {status.reported_at}, reporting {status.reporting}")  # noqa: T201
    for source in status.sources:
        print(f"  {source.name}: {source.state} ({source.detail}); devices {source.devices}; model {source.model_registered}")  # noqa: T201
    assert status.registered
    assert status.status_device, "make it with create_status_device()"
    assert status.reporting
    assert status.source("entsoe").state is SourceState.ENABLED


def test_instances(providers: DataProvidersClient) -> None:
    for instance in providers.instances():
        print(f"\n  {instance.id}: {instance.settings!r} {instance.health}, asset {instance.asset_id}", end="")  # noqa: T201
        assert instance.source
        assert "primaryKey" not in instance.model_dump_json()


def test_create_read_and_remove(providers: DataProvidersClient) -> None:
    zone = os.environ.get("BEACONTOWER_BIDDING_ZONE", "FI")
    instance_id = f"sdk-live-{zone.lower()}-{int(time.time())}"
    source = Entsoe(bidding_zone=zone)
    try:
        instance = providers.create(
            source, instance_id=instance_id, organization_id=os.environ.get("BEACONTOWER_ORGANIZATION"), wait=True, timeout=240
        )
        assert instance.health == "ok"
        assert instance.last_poll is not None
        assert instance.last_poll.points
        assert instance.config == {"source": "entsoe", "biddingZone": zone, "dataType": "dayAheadPrices"}
        assert instance.asset_id == instance_id

        found = providers.find(source)
        assert found is not None
        assert found.source == "entsoe"

        # Through timeseries/get: TelemetryClient's timeseries/query is not routed by every gateway (0.7.0 answers 404).
        now = datetime.now(UTC)
        query = {
            "assetId": instance_id,
            "timeseriesName": ["dayAheadPrice"],
            "searchSpanFrom": (now - timedelta(days=2)).isoformat(),
            "searchSpanTo": (now + timedelta(days=2)).isoformat(),
        }
        points: list[object] = []
        for _ in range(12):  # the points reach the timeseries store shortly after the poll
            answer = providers.client.request("POST", "timeseries/get", json=query).json()
            points = [point for series in answer.get("items", []) for point in series.get("items", [])]
            if points:
                break
            time.sleep(5)
        print(f"\n  {instance_id}: {instance.last_poll.points} points published, {len(points)} read back")  # noqa: T201
        assert points
    finally:
        try:
            providers.remove(instance_id, delete_asset=True)
        except InstanceNotFoundError:
            pass
    with pytest.raises(InstanceNotFoundError):
        providers.get(instance_id)
