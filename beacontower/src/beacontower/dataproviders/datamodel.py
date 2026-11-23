"""What the data provider client returns: the provider's status, its sources and their instances."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from beacontower.dataproviders.sources import OtherSource, Source  # noqa: TC001 -- pydantic reads the field types

#: A status report older than this many of its intervals means the provider is not reporting.
STALE_AFTER_INTERVALS = 3


class SourceState(StrEnum):
    """Whether an environment polls a source.

    ``enabled``: polled. ``disabled``: not switched on in this environment. ``invalid``: switched on
    with settings that cannot be used, such as a missing credential; not polled. ``unknown``: the
    provider's status cannot be read (no status device, or the provider is not registered).
    """

    ENABLED = "enabled"
    DISABLED = "disabled"
    INVALID = "invalid"
    UNKNOWN = "unknown"


class SourceStatus(BaseModel):
    """One source of the provider in this environment.

    `state` and `detail` are the provider's own (what it polls and how often, or why it cannot).
    `devices` counts the source's instances by health (``ok``, ``pending``, ``failing``,
    ``stale``, ``disabled``, ``configError``). `model_registered` says whether the platform has the
    model an instance's asset needs; `supported` whether this package has a class for the source.
    """

    name: str
    state: SourceState
    detail: str | None = None
    devices: dict[str, int] = Field(default_factory=dict)
    model_id: str
    model_registered: bool
    supported: bool

    @property
    def available(self) -> bool:
        """Instances can be made and are polled: the source is enabled and its model registered."""
        return self.state is SourceState.ENABLED and self.model_registered


class ProviderStatus(BaseModel):
    """The data provider in this environment, as the platform's API shows it.

    `registered`: the platform knows the provider. `status_device`: the provider's status device
    exists; without it the sources' states are unknown. `version`, `reported_at` and
    `status_interval` come from the provider's last status report.
    """

    registered: bool
    status_device: bool
    version: str | None = None
    reported_at: datetime | None = None
    status_interval: timedelta | None = None
    sources: list[SourceStatus] = Field(default_factory=list)

    @property
    def reporting(self) -> bool | None:
        """Whether the last report is recent (within three status intervals); None without one."""
        if self.reported_at is None or self.status_interval is None:
            return None
        return datetime.now(UTC) - self.reported_at <= self.status_interval * STALE_AFTER_INTERVALS

    def source(self, name: str) -> SourceStatus:
        """Return the source `name`; KeyError when neither the provider nor this package has it."""
        for source in self.sources:
            if source.name == name:
                return source
        raise KeyError(f"no source {name!r}; known: {[s.name for s in self.sources]}")


class _Reported(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, frozen=True)


class LastPoll(_Reported):
    """The instance's last regular poll: when, its outcome and how many points it published.

    `outcome` is ``succeeded``, ``failed`` (worth retrying) or ``rejected`` (the upstream refused
    the request); `error` says why a poll did not succeed.
    """

    at: datetime
    outcome: str
    points: int | None = None
    error: str | None = None


class History(_Reported):
    """How far back the instance's data reaches, and a range a poll missed, while it is fetched."""

    covered_from: datetime | None = None
    gap_from: datetime | None = None
    gap_to: datetime | None = None


class Instance(BaseModel):
    """One instance of a data provider: a provider client, and the asset that holds its data.

    `settings` is what the instance was provisioned with, read into its source's class
    (:class:`OtherSource` for a source this package does not know). `config` is the identity
    the provider locked at the first successful poll, `config_error` why the instance is not
    polled, `config_mismatch` tags that were changed after the lock and are ignored. `reported`
    holds every reported property as the provider wrote it, such as Open-Meteo's ``grid``.

    `enabled` is the platform's flag for the client. The provider does not act on it: an
    instance whose flag is cleared is still polled. Remove the instance to stop it.
    """

    id: str
    display_name: str
    source: str
    settings: Source | OtherSource
    tags: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    provisioned: bool = False
    provisioned_at: datetime | None = None
    asset_id: str | None = None
    model_id: str | None = None
    config: dict[str, Any] | None = None
    config_error: str | None = None
    config_mismatch: dict[str, Any] | None = None
    last_poll: LastPoll | None = None
    last_success_at: datetime | None = None
    history: History | None = None
    units: dict[str, str] = Field(default_factory=dict)
    reported: dict[str, Any] = Field(default_factory=dict)

    @property
    def health(self) -> str:
        """``unprovisioned``, ``configError``, ``pending`` (not polled yet), ``failing`` or ``ok``.

        Whether an instance is stale (no success for several poll intervals) is counted by the
        provider; see :attr:`SourceStatus.devices`.
        """
        if not self.provisioned:
            return "unprovisioned"
        if self.config_error:
            return "configError"
        if self.last_poll is None:
            return "pending"
        return "ok" if self.last_poll.outcome == "succeeded" else "failing"
