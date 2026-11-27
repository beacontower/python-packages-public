"""Client for Beacon Tower's API provider: its sources, and their instances."""

from __future__ import annotations

import re
import time
import warnings
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import httpx

from beacontower.apiproviders.datamodel import History, Instance, LastPoll, ProviderStatus, SourceState, SourceStatus
from beacontower.apiproviders.sources import SOURCE_TAG, Source, source_class, source_classes

if TYPE_CHECKING:
    from collections.abc import Mapping

    from beacontower.metadata.client import BeaconTowerClient

#: The API provider's id in the platform.
PROVIDER_ID = "apiproviders"

#: The provider client the provider reports every source's state to (device tag ``source: status``).
STATUS_DEVICE_ID = "apiproviders-status"

_STATUS_SOURCE = "status"
_TEMPLATE_NAME = "API providers"
# An endpoint is "<provider>::<client>.<signal>", so a client id must not hold a dot.
_INSTANCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


class ApiProvidersError(Exception):
    """An API provider call that cannot be done as asked."""


class SourceNotAvailableError(ApiProvidersError):
    """The platform lacks what an instance of the source needs: the provider, or the model."""


class InstanceExistsError(ApiProvidersError):
    """A provider client or asset with the instance's id exists already."""


class InstanceNotFoundError(ApiProvidersError):
    """No instance has the id."""


class InstanceConfigError(ApiProvidersError):
    """The provider does not poll the instance; the message is its reported configuration error."""


def _model_id_of(name: str) -> str:
    """Return the model of the source `name`: its class's, else by the provider's convention."""
    cls = source_class(name)
    return cls.model_id if cls is not None else f"dtmi:apiproviders_{name};1.1"


def _interval(text: object) -> timedelta | None:
    """``00:05:00`` (or ``1.00:00:00``) as a timedelta; None for anything else."""
    if not isinstance(text, str) or not (match := re.fullmatch(r"(?:(\d+)\.)?(\d+):(\d+):(\d+)", text)):
        return None
    days, hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)


class ApiProvidersClient:
    """The API provider of one Beacon Tower tenant, through its API.

    Built on a :class:`~beacontower.metadata.BeaconTowerClient`, whose address and login it uses::

        bt = BeaconTowerClient("https://<your-beacon-tower>", "<user name>", "<password>")
        providers = ApiProvidersClient(bt)
        providers.status()                                   # which sources are enabled
        providers.ensure(OpenMeteo(latitude=55.605, longitude=13.0038))

    An instance is a provider client, provisioned with its source's settings as device tags,
    and an asset with the same id on the source's model, bound to the client's signals: the
    asset holds the data. Reading it is :class:`~beacontower.timeseries.TelemetryClient`'s job.
    """

    def __init__(self, client: BeaconTowerClient) -> None:
        """Use `client`'s address and login."""
        self.client = client

    # -- requests ---------------------------------------------------------------------------------

    def _get(self, path: str) -> Any:
        """GET `path` as JSON; None when the API answers 404."""
        try:
            response = self.client.request("GET", path)
        except httpx.HTTPStatusError as error:
            if error.response.status_code == httpx.codes.NOT_FOUND:
                return None
            raise
        return response.json() if response.content else None

    def _send(self, method: str, path: str, body: Mapping[str, object] | None = None) -> None:
        self.client.request(method, path, json=body)

    def _client_path(self, instance_id: str, rest: str = "") -> str:
        return f"providers/{PROVIDER_ID}/clients/{instance_id}{rest}"

    # -- status -----------------------------------------------------------------------------------

    def status(self) -> ProviderStatus:
        """Return the provider's status: whether it is registered and which sources it polls.

        The sources' states come from the provider's status device; without one they are
        ``unknown`` (:meth:`create_status_device` makes it). Every source the provider reports
        is listed, and every source this package has a class for.
        """
        if self._get(f"providers/{PROVIDER_ID}") is None:
            return ProviderStatus(registered=False, status_device=False, sources=self._sources({}, known=[]))
        device = self._get(self._client_path(STATUS_DEVICE_ID))
        report: dict[str, Any] = (self._get(self._client_path(STATUS_DEVICE_ID, "/reportedProperties")) or {}) if device else {}
        provider = _object(report.get("provider"))
        known = report.get("known")
        known = known if isinstance(known, list) else []
        return ProviderStatus(
            registered=True,
            status_device=device is not None,
            version=provider.get("version"),
            reported_at=provider.get("reportedAt"),
            status_interval=_interval(provider.get("statusInterval")),
            sources=self._sources(_object(report.get("sources")), known=[str(name) for name in known]),
        )

    def _sources(self, reported: Mapping[str, Any], known: list[str]) -> list[SourceStatus]:
        sources = []
        for name in sorted({*known, *reported, *source_classes()}):
            entry = _object(reported.get(name))
            state = entry.get("state")
            model_id = _model_id_of(name)
            sources.append(
                SourceStatus(
                    name=name,
                    state=SourceState(state) if state in {s.value for s in SourceState} else SourceState.UNKNOWN,
                    detail=entry.get("detail"),
                    devices={key: int(value) for key, value in _object(entry.get("devices")).items()},
                    model_id=model_id,
                    model_registered=self._get(f"models/{model_id}") is not None,
                    supported=source_class(name) is not None,
                )
            )
        return sources

    def create_status_device(self, organization_id: str | None = None, template_id: str | None = None) -> None:
        """Make the provider's status device, once per environment; nothing happens when it exists.

        It is a provider client with the device tag ``source: status`` and no asset; the provider
        reports every source's state to it within a minute. `organization_id` and `template_id`
        are found as for :meth:`create` when not given.
        """
        if self._get(self._client_path(STATUS_DEVICE_ID)) is None:
            self._send(
                "POST",
                f"providers/{PROVIDER_ID}/clients",
                {"id": STATUS_DEVICE_ID, "displayName": "API providers status", "organizationId": organization_id or self._organization()},
            )
        tags = {SOURCE_TAG: _STATUS_SOURCE}
        self._send(
            "POST",
            self._client_path(STATUS_DEVICE_ID, "/provision"),
            {"templateId": template_id or self._template(), "options": {"tags": tags}},
        )

    # -- instances --------------------------------------------------------------------------------

    def instances(self, source: str | type[Source] | None = None) -> list[Instance]:
        """Every instance, or those of one source (its name or class), with settings and state.

        Three requests per instance: the client, its reported properties and its asset.
        """
        name = source if isinstance(source, str) or source is None else source.name
        found = []
        for entry in self._get(f"providers/{PROVIDER_ID}/clients") or []:
            if entry.get("id") == STATUS_DEVICE_ID:
                continue
            instance = self._instance(entry["id"])
            if instance is not None and (name is None or instance.source == name):
                found.append(instance)
        return found

    def get(self, instance_id: str) -> Instance:
        """Return the instance `instance_id`; InstanceNotFoundError when there is none."""
        instance = self._instance(instance_id)
        if instance is None:
            raise InstanceNotFoundError(f"no instance {instance_id!r}")
        return instance

    def _instance(self, instance_id: str) -> Instance | None:
        client = self._get(self._client_path(instance_id))
        if client is None:
            return None
        provisioning = client.get("provisioningData") or {}
        tags: dict[str, Any] = (provisioning.get("options") or {}).get("tags") or {}
        if tags.get(SOURCE_TAG) == _STATUS_SOURCE:
            return None
        reported: dict[str, Any] = (self._get(self._client_path(instance_id, "/reportedProperties")) or {}) if provisioning else {}
        config = reported.get("config") if isinstance(reported.get("config"), dict) else None
        settings = Source.from_tags(tags)
        asset = self._get(f"assets/{instance_id}")
        return Instance(
            id=instance_id,
            display_name=client.get("displayName") or instance_id,
            source=str((config or {}).get(SOURCE_TAG) or tags.get(SOURCE_TAG) or ""),
            settings=settings,
            tags=tags,
            enabled=bool(client.get("enabled", True)),
            provisioned=bool(provisioning),
            provisioned_at=provisioning.get("timestamp"),
            asset_id=asset.get("id") if asset else None,
            model_id=asset.get("modelId") if asset else None,
            config=config,
            config_error=reported.get("configError"),
            config_mismatch=reported.get("configMismatch"),
            last_poll=LastPoll.model_validate(reported["lastPoll"]) if isinstance(reported.get("lastPoll"), dict) else None,
            last_success_at=reported.get("lastSuccessAt"),
            history=History.model_validate(reported["history"]) if isinstance(reported.get("history"), dict) else None,
            units=reported.get("units") or {},
            reported=reported,
        )

    def find(self, source: Source) -> Instance | None:
        """Return the instance of `source`'s source with the same identity settings, or None.

        An instance's identity is what the provider locked at its first successful poll, or,
        before that, its tags. For Open-Meteo that is the position, compared exactly.
        """
        wanted = _normalized(source.identity_values())
        for instance in self.instances(source.name):
            locked = instance.config or {}
            values = tuple(locked[key] if key in locked else instance.tags.get(key) for key in source.identity)
            if _normalized(values) == wanted:
                return instance
        return None

    def create(  # noqa: PLR0913
        self,
        source: Source,
        *,
        instance_id: str | None = None,
        display_name: str | None = None,
        organization_id: str | None = None,
        template_id: str | None = None,
        wait: bool = False,
        timeout: float = 300.0,
    ) -> Instance:
        """Make an instance of `source` and return it.

        The provider client, then the asset (same id, the source's model), then the binding of
        the asset to the client's signals, and last the provisioning with the source's settings:
        the provider polls within a minute of it, and points that arrive before the binding are
        lost. When a step fails, the steps done are undone.

        `instance_id` defaults to :meth:`Source.default_instance_id`, `display_name` to the id.
        `organization_id` defaults to the only organization the login sees; `template_id` to the
        provider template named "API providers", else the only enabled template for ``sdk``
        providers. With `wait`, returns after the first successful poll
        (:meth:`wait_for_first_poll`).
        """
        instance_id = instance_id or source.default_instance_id()
        if not _INSTANCE_ID.match(instance_id):
            raise ValueError(f"an instance id is letters, digits, '-' and '_' (no '.'), at most 128: {instance_id!r}")
        if self._get(self._client_path(instance_id)) is not None:
            raise InstanceExistsError(f"a provider client {instance_id!r} exists already")
        if self._get(f"assets/{instance_id}") is not None:
            raise InstanceExistsError(f"an asset {instance_id!r} exists already")
        self._check_available(source)
        organization_id = organization_id or self._organization()
        template_id = template_id or self._template()
        display_name = display_name or instance_id

        made: list[str] = []
        try:
            self._send(
                "POST",
                f"providers/{PROVIDER_ID}/clients",
                {"id": instance_id, "displayName": display_name, "organizationId": organization_id},
            )
            made.append(self._client_path(instance_id))
            asset = {"id": instance_id, "displayName": display_name, "organizationId": organization_id, "modelId": source.model_id}
            self._send("POST", "assets", asset)
            made.append(f"assets/{instance_id}")
            self._send("POST", f"assets/{instance_id}/bind", {"autobindRemoteInstanceIds": [f"{PROVIDER_ID}::{instance_id}"]})
            self._send(
                "POST", self._client_path(instance_id, "/provision"), {"templateId": template_id, "options": {"tags": source.tags()}}
            )
        except Exception:
            for path in reversed(made):
                try:
                    self._send("DELETE", path)
                except httpx.HTTPError as undo:
                    warnings.warn(f"could not undo {path} after a failed create: {undo}", stacklevel=2)
            raise
        return self.wait_for_first_poll(instance_id, timeout=timeout) if wait else self.get(instance_id)

    def ensure(  # noqa: PLR0913
        self,
        source: Source,
        *,
        instance_id: str | None = None,
        display_name: str | None = None,
        organization_id: str | None = None,
        template_id: str | None = None,
        wait: bool = False,
        timeout: float = 300.0,
    ) -> Instance:
        """Return the instance with `source`'s identity (:meth:`find`), made when there is none.

        The way to add weather for a position without paying twice for it. The arguments are
        :meth:`create`'s and apply only when an instance is made.
        """
        found = self.find(source)
        if found is not None:
            return found
        return self.create(
            source,
            instance_id=instance_id,
            display_name=display_name,
            organization_id=organization_id,
            template_id=template_id,
            wait=wait,
            timeout=timeout,
        )

    def remove(self, instance_id: str, *, delete_asset: bool = False) -> None:
        """Stop and remove an instance: unprovision its client, then delete it.

        Its asset and the data already stored stay, unless `delete_asset`; whether deleting the
        asset deletes its stored data is the platform's matter. To move an Open-Meteo instance,
        remove it and make a new one: the position is locked at the first successful poll.
        """
        if instance_id == STATUS_DEVICE_ID:
            raise ValueError("the status device is not an instance; the provider reports every source's state to it")
        client = self._get(self._client_path(instance_id))
        if client is None:
            raise InstanceNotFoundError(f"no instance {instance_id!r}")
        if client.get("provisioningData"):
            self._send("POST", self._client_path(instance_id, "/unprovision"))
        self._send("DELETE", self._client_path(instance_id))
        if delete_asset and self._get(f"assets/{instance_id}") is not None:
            self._send("DELETE", f"assets/{instance_id}")

    def wait_for_first_poll(self, instance_id: str, *, timeout: float = 300.0, interval: float = 5.0) -> Instance:
        """Wait until the instance's first poll succeeds, and return it.

        Raises InstanceConfigError as soon as the provider reports that it does not poll the
        instance (its settings, or its source not enabled), and TimeoutError after `timeout`
        seconds, with the last poll's error when there was one. History beyond the regular poll
        arrives over the following minutes.
        """
        deadline = time.monotonic() + timeout
        while True:
            instance = self.get(instance_id)
            if instance.last_success_at is not None:
                return instance
            if instance.config_error:
                raise InstanceConfigError(f"{instance_id}: {instance.config_error}")
            if time.monotonic() >= deadline:
                last = f"; last poll {instance.last_poll.outcome}: {instance.last_poll.error}" if instance.last_poll else ""
                raise TimeoutError(f"{instance_id} has had no successful poll within {timeout:g} s{last}")
            time.sleep(interval)

    # -- defaults ---------------------------------------------------------------------------------

    def _check_available(self, source: Source) -> None:
        """Raise unless the platform has the provider and the model; warn when the source is off."""
        if self._get(f"providers/{PROVIDER_ID}") is None:
            raise SourceNotAvailableError(f"the provider {PROVIDER_ID!r} is not registered in this environment")
        model = self._get(f"models/{source.model_id}")
        if model is None:
            raise SourceNotAvailableError(f"the model {source.model_id} of source {source.name!r} is not registered in this environment")
        source.check_model(_channels(model))
        report = self._get(self._client_path(STATUS_DEVICE_ID, "/reportedProperties")) or {}
        state = ((report.get("sources") or {}).get(source.name) or {}).get("state")
        if state in {SourceState.DISABLED, SourceState.INVALID}:
            warnings.warn(
                f"source {source.name!r} is {state} in this environment: the instance is made, and polled once the source is enabled",
                stacklevel=3,
            )

    def _organization(self) -> str:
        organizations = self._get("organizations") or []
        if len(organizations) == 1:
            return str(organizations[0]["id"])
        names = {o.get("displayName"): o.get("id") for o in organizations}
        raise ValueError(f"pass organization_id: the login sees {len(organizations)} organizations: {names}")

    def _template(self) -> str:
        templates = [t for t in self._get("providerTemplates") or [] if t.get("enabled", True)]
        if named := [t for t in templates if t.get("displayName") == _TEMPLATE_NAME]:
            return str(named[0]["id"])
        sdk = [t for t in templates if "sdk" in ((t.get("tags") or {}).get("providerType") or [])]
        if len(sdk) == 1:
            return str(sdk[0]["id"])
        names = {t.get("displayName"): t.get("id") for t in templates}
        raise ValueError(f"pass template_id: no template is named {_TEMPLATE_NAME!r}, and {len(sdk)} are for sdk providers: {names}")


def _object(value: object) -> dict[str, Any]:
    """Return `value` when it is a JSON object, else an empty one."""
    return value if isinstance(value, dict) else {}


def _channels(model: Mapping[str, Any]) -> set[str]:
    """Return the names of the telemetry channels of a model, as GET models/{id} returns it."""
    names = set()
    for interface in model.get("modelData") or []:
        for content in interface.get("contents") or []:
            types = content.get("@type")
            if "Telemetry" in ([types] if isinstance(types, str) else types or []):
                names.add(content.get("name"))
    return names


def _normalized(values: tuple[Any, ...]) -> tuple[Any, ...]:
    """Return numbers as floats, so a position in tags matches one given in Python."""
    return tuple(float(v) if isinstance(v, int | float) and not isinstance(v, bool) else v for v in values)


__all__ = [
    "PROVIDER_ID",
    "STATUS_DEVICE_ID",
    "ApiProvidersClient",
    "ApiProvidersError",
    "InstanceConfigError",
    "InstanceExistsError",
    "InstanceNotFoundError",
    "SourceNotAvailableError",
]
