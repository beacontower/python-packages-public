"""ApiProvidersClient against an in-memory Beacon Tower API."""

import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from beacontower.apiproviders import (
    ApiProvidersClient,
    Entsoe,
    InstanceConfigError,
    InstanceExistsError,
    InstanceNotFoundError,
    OpenMeteo,
    OtherSource,
    Riksbank,
    SourceNotAvailableError,
    SourceState,
)
from beacontower.apiproviders import client as client_module
from beacontower.metadata.client import BeaconTowerClient

BASE = "http://gateway/api/v1/"
OPEN_METEO_CHANNELS = ["temperature_2m", "precipitation", "wind_speed_10m"]


def _model(model_id: str, channels: list[str]) -> dict[str, Any]:
    contents = [{"@type": "Telemetry", "name": name, "schema": "double"} for name in channels]
    return {"id": model_id, "modelData": [{"@id": model_id, "@type": "Interface", "contents": contents}]}


class FakeTower:
    """The parts of the platform's API the API provider client uses, in memory."""

    def __init__(self) -> None:
        self.provider = True
        self.organizations = [{"id": "org-1", "displayName": "Org"}]
        self.templates = [
            {"id": "tpl-mqtt", "displayName": "Default", "enabled": True, "tags": {"providerType": ["mqtt"]}},
            {"id": "tpl-dp", "displayName": "API providers", "enabled": True, "tags": {"providerType": ["sdk"]}},
        ]
        self.models = {
            OpenMeteo.model_id: _model(OpenMeteo.model_id, OPEN_METEO_CHANNELS),
            Entsoe.model_id: _model(Entsoe.model_id, ["dayAheadPrice"]),
        }
        self.clients: dict[str, dict[str, Any]] = {}
        self.reported: dict[str, dict[str, Any]] = {}
        self.assets: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str, Any]] = []
        self.failing: set[tuple[str, str]] = set()

    def add_instance(
        self, instance_id: str, tags: dict[str, Any], reported: dict[str, Any] | None = None, model_id: str | None = None
    ) -> None:
        provisioning = {"timestamp": "2026-11-22T08:00:00Z", "templateId": "tpl-dp", "options": {"tags": tags}}
        self.clients[instance_id] = {
            "id": instance_id,
            "providerId": "apiproviders",
            "displayName": instance_id,
            "enabled": True,
            "provisioningData": provisioning,
            "providerData": {"state": "Enabled", "primaryKey": "secret", "secondaryKey": "secret"},
        }
        self.reported[instance_id] = reported or {}
        if model_id:
            self.assets[instance_id] = {"id": instance_id, "modelId": model_id}

    def add_status_device(self, sources: dict[str, Any], reported_at: str = "2026-11-22T08:00:00Z") -> None:
        self.add_instance("apiproviders-status", {"source": "status"})
        self.reported["apiproviders-status"] = {
            "provider": {"version": "0.2.0", "reportedAt": reported_at, "statusInterval": "00:05:00"},
            "known": sorted(sources),
            "sources": sources,
        }

    def handle(self, method: str, url: str, body: Any = None) -> httpx.Response:  # noqa: C901, PLR0911, PLR0912
        path = url.removeprefix(BASE)
        self.calls.append((method, path, body))
        if (method, path) in self.failing:
            return self._answer(method, url, 500, {"error": "boom"})
        clients = "providers/apiproviders/clients"
        if path == "providers/apiproviders" and method == "GET":
            return self._answer(method, url, 200 if self.provider else 404, {"id": "apiproviders"})
        if path == "organizations":
            return self._answer(method, url, 200, self.organizations)
        if path == "providerTemplates":
            return self._answer(method, url, 200, self.templates)
        if path.startswith("models/"):
            model = self.models.get(path.removeprefix("models/"))
            return self._answer(method, url, 200 if model else 404, model)
        if path == clients and method == "GET":
            return self._answer(
                method, url, 200, [{k: c[k] for k in ("id", "providerId", "displayName", "enabled")} for c in self.clients.values()]
            )
        if path == clients and method == "POST":
            self.clients[body["id"]] = {**body, "providerId": "apiproviders", "enabled": True}
            return self._answer(method, url, 200, body)
        if match := re.fullmatch(rf"{clients}/([^/]+)(/\w+)?", path):
            instance_id, rest = match.groups()
            client = self.clients.get(instance_id)
            if client is None:
                return self._answer(method, url, 404, None)
            if rest is None and method == "GET":
                return self._answer(method, url, 200, client)
            if rest is None and method == "PATCH":
                client.update(body)
                return self._answer(method, url, 200, client)
            if rest is None and method == "DELETE":
                del self.clients[instance_id]
                return self._answer(method, url, 200, None)
            if rest == "/reportedProperties":
                return self._answer(method, url, 200, self.reported.get(instance_id, {}))
            if rest == "/provision":
                client["provisioningData"] = {"timestamp": "2026-11-22T08:00:00Z", **body}
                return self._answer(method, url, 200, None)
            if rest == "/unprovision":
                client["provisioningData"] = None
                return self._answer(method, url, 200, None)
        if path == "assets" and method == "POST":
            self.assets[body["id"]] = dict(body)
            return self._answer(method, url, 201, body)
        if match := re.fullmatch(r"assets/([^/]+)(/bind)?", path):
            asset_id, bind = match.groups()
            if asset_id not in self.assets:
                return self._answer(method, url, 404, None)
            if bind:
                self.assets[asset_id]["bindingData"] = body
                return self._answer(method, url, 200, {"endpointBindResults": []})
            if method == "DELETE":
                del self.assets[asset_id]
                return self._answer(method, url, 204, None)
            return self._answer(method, url, 200, self.assets[asset_id])
        return self._answer(method, url, 404, None)

    @staticmethod
    def _answer(method: str, url: str, status: int, body: Any) -> httpx.Response:
        request = httpx.Request(method, url)
        if body is None or status == 204:  # noqa: PLR2004
            return httpx.Response(status, request=request)
        return httpx.Response(status, json=body, request=request)

    def writes(self) -> list[tuple[str, str]]:
        return [(m, p) for m, p, _ in self.calls if m != "GET"]


@pytest.fixture
def tower(monkeypatch: pytest.MonkeyPatch) -> FakeTower:
    fake = FakeTower()
    for method in ("get", "post", "put", "patch", "delete"):
        monkeypatch.setattr(httpx, method, lambda url, *, _m=method.upper(), json=None, **_: fake.handle(_m, url, json))
    return fake


@pytest.fixture
def providers(tower: FakeTower) -> ApiProvidersClient:
    bt = BeaconTowerClient(BASE, "user", "pw", resolve_url=False)
    bt.header = {"Authorization": "Bearer test"}
    return ApiProvidersClient(bt)


ENABLED = {"state": "enabled", "detail": "Open-Meteo (...), every 01:00:00", "devices": {"ok": 1, "pending": 0}}


# -- status ------------------------------------------------------------------------------------------


def test_status_reads_the_status_device(tower: FakeTower, providers: ApiProvidersClient) -> None:
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    tower.add_status_device(
        {
            "openmeteo": ENABLED,
            "entsoe": {"state": "invalid", "detail": "Sources:Entsoe:SecurityToken is required", "devices": {}},
            "riksbank": {"state": "disabled", "detail": "not enabled in this environment", "devices": {}},
        },
        reported_at=now,
    )

    status = providers.status()

    assert status.registered
    assert status.status_device
    assert status.version == "0.2.0"
    assert status.status_interval == timedelta(minutes=5)
    assert status.reporting is True
    assert [(s.name, s.state, s.model_registered, s.supported) for s in status.sources] == [
        ("entsoe", SourceState.INVALID, True, True),
        ("openmeteo", SourceState.ENABLED, True, True),
        ("riksbank", SourceState.DISABLED, False, True),
    ]
    assert status.source("openmeteo").available
    assert not status.source("riksbank").available
    assert status.source("openmeteo").devices == {"ok": 1, "pending": 0}


def test_an_old_report_means_the_provider_is_not_reporting(tower: FakeTower, providers: ApiProvidersClient) -> None:
    tower.add_status_device({"openmeteo": ENABLED}, reported_at="2026-01-01T00:00:00Z")
    assert providers.status().reporting is False


def test_a_source_the_package_does_not_know_is_listed(tower: FakeTower, providers: ApiProvidersClient) -> None:
    tower.add_status_device({"nordpool": {"state": "enabled", "detail": "x", "devices": {}}})
    nordpool = providers.status().source("nordpool")
    assert nordpool.state is SourceState.ENABLED
    assert not nordpool.supported
    assert nordpool.model_id == "dtmi:apiproviders_nordpool;1.1"


def test_without_a_status_device_the_states_are_unknown(providers: ApiProvidersClient) -> None:
    status = providers.status()
    assert status.registered
    assert not status.status_device
    assert status.reporting is None
    assert {s.state for s in status.sources} == {SourceState.UNKNOWN}


def test_an_unregistered_provider(tower: FakeTower, providers: ApiProvidersClient) -> None:
    tower.provider = False
    status = providers.status()
    assert not status.registered
    assert [s.name for s in status.sources] == ["entsoe", "openmeteo", "riksbank"]


def test_the_status_device_is_made_once(tower: FakeTower, providers: ApiProvidersClient) -> None:
    providers.create_status_device()
    providers.create_status_device()

    assert tower.writes().count(("POST", "providers/apiproviders/clients")) == 1
    assert tower.clients["apiproviders-status"]["provisioningData"]["options"] == {"tags": {"source": "status"}}
    assert tower.clients["apiproviders-status"]["provisioningData"]["templateId"] == "tpl-dp"


# -- instances ---------------------------------------------------------------------------------------


def _malmo(tower: FakeTower) -> None:
    tower.add_instance(
        "weather-malmo",
        {"source": "openmeteo", "latitude": 55.605, "longitude": 13.0038, "historyDays": 30},
        {
            "config": {"source": "openmeteo", "latitude": 55.605, "longitude": 13.0038},
            "lastPoll": {"at": "2026-11-22T07:56:41Z", "points": 529, "outcome": "succeeded"},
            "lastSuccessAt": "2026-11-22T07:56:41Z",
            "history": {"key": "x", "coveredFrom": "2026-10-23T00:00:00Z"},
            "units": {"temperature_2m": "°C"},
            "grid": {"latitude": 55.606525, "longitude": 13.002045, "elevation": 11},
        },
        model_id=OpenMeteo.model_id,
    )


def test_instances_carry_settings_and_state(tower: FakeTower, providers: ApiProvidersClient) -> None:
    _malmo(tower)
    tower.add_instance("prices-se3", {"source": "entsoe", "biddingZone": "SE3", "dataType": "dayAheadPrices"}, model_id=Entsoe.model_id)
    tower.add_status_device({"openmeteo": ENABLED})

    instances = {i.id: i for i in providers.instances()}

    assert set(instances) == {"weather-malmo", "prices-se3"}, "the status device is not an instance"
    malmo = instances["weather-malmo"]
    assert malmo.source == "openmeteo"
    assert malmo.settings == OpenMeteo(latitude=55.605, longitude=13.0038, history_days=30)
    assert malmo.health == "ok"
    assert malmo.last_poll is not None
    assert malmo.last_poll.points == 529  # noqa: PLR2004
    assert malmo.history is not None
    assert malmo.history.covered_from == datetime(2026, 10, 23, tzinfo=UTC)
    assert malmo.asset_id == "weather-malmo"
    assert malmo.model_id == OpenMeteo.model_id
    assert malmo.units == {"temperature_2m": "°C"}
    assert malmo.reported["grid"]["latitude"] == 55.606525  # noqa: PLR2004
    assert instances["prices-se3"].health == "pending"
    assert "secret" not in malmo.model_dump_json(), "the device keys are not kept"


def test_instances_of_one_source(tower: FakeTower, providers: ApiProvidersClient) -> None:
    _malmo(tower)
    tower.add_instance("sek-rates", {"source": "riksbank"})
    assert [i.id for i in providers.instances(Riksbank)] == ["sek-rates"]
    assert [i.id for i in providers.instances("openmeteo")] == ["weather-malmo"]


def test_an_instance_of_an_unknown_source(tower: FakeTower, providers: ApiProvidersClient) -> None:
    tower.add_instance("x", {"source": "nordpool", "area": "SE3"}, {"configError": "source must be one of: entsoe, openmeteo, riksbank"})
    instance = providers.get("x")
    assert instance.settings == OtherSource(source="nordpool", settings={"area": "SE3"})
    assert instance.health == "configError"


def test_get_an_instance_that_does_not_exist(providers: ApiProvidersClient) -> None:
    with pytest.raises(InstanceNotFoundError):
        providers.get("nope")


# -- create ------------------------------------------------------------------------------------------


def test_create_makes_client_asset_binding_then_provisions(tower: FakeTower, providers: ApiProvidersClient) -> None:
    instance = providers.create(OpenMeteo(latitude=55.605, longitude=13.0038, history_days=30))

    assert instance.id == "openmeteo-55_6050n-13_0038e"
    assert tower.writes() == [
        ("POST", "providers/apiproviders/clients"),
        ("POST", "assets"),
        ("POST", "assets/openmeteo-55_6050n-13_0038e/bind"),
        ("POST", "providers/apiproviders/clients/openmeteo-55_6050n-13_0038e/provision"),
    ]
    bodies = {p: b for m, p, b in tower.calls if m == "POST"}
    assert bodies["assets"] == {
        "id": "openmeteo-55_6050n-13_0038e",
        "displayName": "openmeteo-55_6050n-13_0038e",
        "organizationId": "org-1",
        "modelId": "dtmi:apiproviders_openmeteo;1.1",
    }
    assert bodies["assets/openmeteo-55_6050n-13_0038e/bind"] == {"autobindRemoteInstanceIds": ["apiproviders::openmeteo-55_6050n-13_0038e"]}
    assert bodies["providers/apiproviders/clients/openmeteo-55_6050n-13_0038e/provision"] == {
        "templateId": "tpl-dp",
        "options": {"tags": {"source": "openmeteo", "historyDays": 30, "latitude": 55.605, "longitude": 13.0038}},
    }
    assert instance.settings == OpenMeteo(latitude=55.605, longitude=13.0038, history_days=30)


def test_a_failed_create_is_undone(tower: FakeTower, providers: ApiProvidersClient) -> None:
    tower.failing.add(("POST", "providers/apiproviders/clients/prices-se2/provision"))

    with pytest.raises(httpx.HTTPStatusError):
        providers.create(Entsoe(bidding_zone="SE2"), instance_id="prices-se2")

    assert tower.writes()[-2:] == [("DELETE", "assets/prices-se2"), ("DELETE", "providers/apiproviders/clients/prices-se2")]
    assert not tower.clients
    assert not tower.assets


def test_create_refuses_an_existing_id(tower: FakeTower, providers: ApiProvidersClient) -> None:
    _malmo(tower)
    with pytest.raises(InstanceExistsError):
        providers.create(OpenMeteo(latitude=55.7047, longitude=13.191), instance_id="weather-malmo")
    tower.assets["lonely"] = {"id": "lonely"}
    with pytest.raises(InstanceExistsError):
        providers.create(OpenMeteo(latitude=55.7047, longitude=13.191), instance_id="lonely")


def test_create_refuses_an_id_with_a_dot(providers: ApiProvidersClient) -> None:
    with pytest.raises(ValueError, match="no '.'"):
        providers.create(Riksbank(), instance_id="rates.sek")


def test_create_needs_the_model(tower: FakeTower, providers: ApiProvidersClient) -> None:
    with pytest.raises(SourceNotAvailableError, match="riksbank"):
        providers.create(Riksbank())
    assert not tower.writes()


def test_create_needs_the_provider(tower: FakeTower, providers: ApiProvidersClient) -> None:
    tower.provider = False
    with pytest.raises(SourceNotAvailableError, match="not registered"):
        providers.create(Entsoe(bidding_zone="SE3"))


def test_create_refuses_variables_the_model_lacks(tower: FakeTower, providers: ApiProvidersClient) -> None:
    with pytest.raises(ValueError, match="snowfall"):
        providers.create(OpenMeteo(latitude=55.7047, longitude=13.191, variables=("temperature_2m", "snowfall")))
    assert not tower.writes()


def test_create_warns_when_the_source_is_not_enabled(tower: FakeTower, providers: ApiProvidersClient) -> None:
    tower.add_status_device({"entsoe": {"state": "disabled", "detail": "not enabled in this environment", "devices": {}}})
    with pytest.warns(UserWarning, match="disabled"):
        providers.create(Entsoe(bidding_zone="SE3"))


def test_create_needs_an_organization_when_there_are_several(tower: FakeTower, providers: ApiProvidersClient) -> None:
    tower.organizations.append({"id": "org-2", "displayName": "Other"})
    with pytest.raises(ValueError, match="organization_id"):
        providers.create(Entsoe(bidding_zone="SE3"))
    providers.create(Entsoe(bidding_zone="SE3"), organization_id="org-2")
    assert tower.clients["entsoe-se3-dayaheadprices"]["organizationId"] == "org-2"


def test_the_only_sdk_template_is_used_without_the_name(tower: FakeTower, providers: ApiProvidersClient) -> None:
    tower.templates[1]["displayName"] = "Something else"
    instance = providers.create(Entsoe(bidding_zone="SE3"))
    assert tower.clients[instance.id]["provisioningData"]["templateId"] == "tpl-dp"


# -- ensure, remove, wait ----------------------------------------------------------------------------


def test_ensure_finds_the_instance_at_the_position(tower: FakeTower, providers: ApiProvidersClient) -> None:
    _malmo(tower)
    instance = providers.ensure(OpenMeteo(latitude=55.6050, longitude=13.0038))
    assert instance.id == "weather-malmo"
    assert not tower.writes()


def test_ensure_compares_the_locked_position(tower: FakeTower, providers: ApiProvidersClient) -> None:
    _malmo(tower)
    tower.clients["weather-malmo"]["provisioningData"]["options"]["tags"]["latitude"] = 57.7089  # changed after the lock
    assert providers.ensure(OpenMeteo(latitude=55.605, longitude=13.0038)).id == "weather-malmo"


def test_ensure_creates_at_another_position(tower: FakeTower, providers: ApiProvidersClient) -> None:
    _malmo(tower)
    instance = providers.ensure(OpenMeteo(latitude=59.3293, longitude=18.0686))
    assert instance.id == "openmeteo-59_3293n-18_0686e"


def test_ensure_finds_the_one_riksbank_instance(tower: FakeTower, providers: ApiProvidersClient) -> None:
    tower.add_instance("sek-rates", {"source": "riksbank", "historyDays": 30})
    assert providers.ensure(Riksbank()).id == "sek-rates"


def test_remove_unprovisions_then_deletes(tower: FakeTower, providers: ApiProvidersClient) -> None:
    _malmo(tower)
    providers.remove("weather-malmo")
    assert tower.writes() == [
        ("POST", "providers/apiproviders/clients/weather-malmo/unprovision"),
        ("DELETE", "providers/apiproviders/clients/weather-malmo"),
    ]
    assert "weather-malmo" in tower.assets, "the asset and its data stay"


def test_remove_can_delete_the_asset(tower: FakeTower, providers: ApiProvidersClient) -> None:
    _malmo(tower)
    providers.remove("weather-malmo", delete_asset=True)
    assert not tower.assets


def test_remove_refuses_the_status_device_and_unknown_ids(tower: FakeTower, providers: ApiProvidersClient) -> None:
    tower.add_status_device({})
    with pytest.raises(ValueError, match="status device"):
        providers.remove("apiproviders-status")
    with pytest.raises(InstanceNotFoundError):
        providers.remove("nope")


def test_wait_returns_after_the_first_success(tower: FakeTower, providers: ApiProvidersClient, monkeypatch: pytest.MonkeyPatch) -> None:
    tower.add_instance("w", {"source": "openmeteo", "latitude": 59.8586, "longitude": 17.6389})
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        tower.reported["w"] = {
            "lastPoll": {"at": "2026-11-22T08:01:00Z", "outcome": "succeeded", "points": 9},
            "lastSuccessAt": "2026-11-22T08:01:00Z",
        }

    monkeypatch.setattr(client_module.time, "sleep", sleep)
    assert providers.wait_for_first_poll("w").health == "ok"
    assert sleeps == [5.0]


def test_wait_raises_the_reported_config_error(tower: FakeTower, providers: ApiProvidersClient) -> None:
    tower.add_instance("w", {"source": "entsoe", "biddingZone": "XX"}, {"configError": "biddingZone XX is not a known bidding zone"})
    with pytest.raises(InstanceConfigError, match="XX is not a known bidding zone"):
        providers.wait_for_first_poll("w")


def test_wait_times_out_with_the_last_error(tower: FakeTower, providers: ApiProvidersClient, monkeypatch: pytest.MonkeyPatch) -> None:
    tower.add_instance(
        "w",
        {"source": "openmeteo", "latitude": 59.8586, "longitude": 17.6389},
        {"lastPoll": {"at": "2026-11-22T08:01:00Z", "outcome": "failed", "error": "HTTP 503"}},
    )
    monkeypatch.setattr(client_module.time, "sleep", lambda _: None)
    clock = iter([0.0, 0.0, 400.0])
    monkeypatch.setattr(client_module.time, "monotonic", lambda: next(clock))
    with pytest.raises(TimeoutError, match="last poll failed: HTTP 503"):
        providers.wait_for_first_poll("w")


# -- the metadata client's request -------------------------------------------------------------------


def test_request_sends_any_method(tower: FakeTower, providers: ApiProvidersClient) -> None:
    _malmo(tower)
    providers.client.request("PATCH", "/providers/apiproviders/clients/weather-malmo", json={"enabled": False})
    assert tower.calls[-1] == ("PATCH", "providers/apiproviders/clients/weather-malmo", {"enabled": False})


def test_request_refuses_a_body_on_get(providers: ApiProvidersClient) -> None:
    with pytest.raises(ValueError, match="no body"):
        providers.client.request("GET", "organizations", json={"x": 1})
