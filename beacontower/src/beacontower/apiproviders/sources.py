"""The sources of Beacon Tower's API provider, one class per source.

An instance of a source is what one provider client fetches: weather at one position, day-ahead
prices in one bidding zone, the Riksbank's exchange rates. Its settings become the client's device
tags when it is provisioned, and the provider reads them from there.

To add a source, subclass :class:`Source` with the source's `name`, `model_id` and `identity`, and
declare its settings as fields; the class registers itself, and :meth:`Source.from_tags` reads
instances of it from then on. Field names are the tags' names in snake_case (``history_days`` is
the tag ``historyDays``).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from pydantic.alias_generators import to_camel

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping

#: The most history an instance can ask for, in days (``historyDays``).
MAX_HISTORY_DAYS = 3660

#: The device tag that names an instance's source.
SOURCE_TAG = "source"

# Tags every device of the provider may carry that are not a source's settings.
_PLATFORM_TAGS = frozenset({SOURCE_TAG, "parser"})

_SOURCES: dict[str, type[Source]] = {}
_VARIABLE = re.compile(r"^[a-z0-9_]+$")
_BIDDING_ZONE = re.compile(r"^[A-Z0-9_]+$")


class Source(BaseModel):
    """The source an instance fetches from, and that source's settings for the instance.

    Every source has `history_days`: how many days of history to fetch once, after the first
    poll (0 to 3660; the regular polls cover each source's recent past anyway). A subclass sets:

    * `name`: the device's ``source`` tag, such as ``openmeteo``;
    * `model_id`: the DTDL model the instance's asset implements, one telemetry channel per signal;
    * `identity`: the tags that tell two instances of the source apart. The provider locks them
      at the first successful poll; changing them afterwards has no effect, so an instance with
      other identity settings is a new instance.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, frozen=True, extra="forbid")

    name: ClassVar[str] = ""
    model_id: ClassVar[str] = ""
    identity: ClassVar[tuple[str, ...]] = ()

    history_days: int = Field(default=0, ge=0, le=MAX_HISTORY_DAYS)

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        """Register a source class under its `name`."""
        super().__pydantic_init_subclass__(**kwargs)
        if cls.name:
            if cls.name in _SOURCES:
                raise TypeError(f"a source named {cls.name!r} is already registered: {_SOURCES[cls.name].__name__}")
            _SOURCES[cls.name] = cls

    def tags(self) -> dict[str, Any]:
        """Return the device tags that provision this instance: ``source`` and every setting.

        A setting without a value is left out, never sent as null.
        """
        return {SOURCE_TAG: self.name, **self.model_dump(by_alias=True, exclude_none=True, mode="json")}

    def identity_values(self) -> tuple[Any, ...]:
        """Return the values of the identity tags, in the order of `identity`."""
        tags = self.tags()
        return tuple(tags.get(key) for key in self.identity)

    def default_instance_id(self) -> str:
        """Return an id for an instance made without one: the source's name and its identity."""
        return self.name

    def check_model(self, channels: Collection[str]) -> None:
        """Raise ValueError when the settings ask for a signal the model has no channel for.

        A signal the asset's model lacks is dropped by the platform without a trace, so a
        source whose signals can be chosen checks them against the model's `channels` here.
        """

    @staticmethod
    def from_tags(tags: Mapping[str, Any]) -> Source | OtherSource:
        """Read an instance's source from its device tags.

        A source this version of the package has no class for, or tags its class cannot read,
        come back as an :class:`OtherSource` holding the tags as they are.
        """
        name = tags.get(SOURCE_TAG)
        settings = {key: value for key, value in tags.items() if key not in _PLATFORM_TAGS}
        source_class = _SOURCES.get(name) if isinstance(name, str) else None
        if source_class is None:
            return OtherSource(source=str(name or ""), settings=settings)
        known = {field.alias or key for key, field in source_class.model_fields.items()}
        try:
            return source_class.model_validate({key: value for key, value in settings.items() if key in known})
        except ValidationError:
            return OtherSource(source=source_class.name, settings=settings)


class OtherSource(BaseModel):
    """A source this version of the package has no class for, or settings it cannot read.

    `source` is the device's ``source`` tag and `settings` its other tags, as they are.
    """

    model_config = ConfigDict(frozen=True)

    source: str
    settings: dict[str, Any] = Field(default_factory=dict)


def source_class(name: str) -> type[Source] | None:
    """Return the class registered for the source `name`, or None."""
    return _SOURCES.get(name)


def source_classes() -> dict[str, type[Source]]:
    """Return every registered source class, by name."""
    return dict(sorted(_SOURCES.items()))


def _coordinate(value: float, positive: str, negative: str) -> str:
    """``55.605`` as ``55_6050n``: no dot, which separates an endpoint's client from its signal."""
    return f"{abs(value):.4f}".replace(".", "_") + (positive if value >= 0 else negative)


class OpenMeteo(Source):
    """Weather at a position, from Open-Meteo's paid plan.

    Hourly: each poll fetches the last 7 days and a 16-day forecast; `history_days` beyond that
    comes from Open-Meteo's historical API. Nine variables by default (temperature, wet-bulb
    temperature, wind speed and direction, precipitation, cloud cover, humidity, shortwave and
    direct radiation); `variables` replaces them, and each must be a channel of the model.
    Open-Meteo bills per position, so reuse an instance rather than making another at the same
    position (:meth:`ApiProvidersClient.ensure`).
    """

    name: ClassVar[str] = "openmeteo"
    model_id: ClassVar[str] = "dtmi:apiproviders_openmeteo;1.1"
    identity: ClassVar[tuple[str, ...]] = ("latitude", "longitude")

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    variables: tuple[str, ...] | None = None

    @field_validator("variables")
    @classmethod
    def _variable_names(cls, variables: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if variables is not None:
            if not variables:
                raise ValueError("variables must name at least one variable, or be left out for the default ones")
            if bad := [v for v in variables if not _VARIABLE.match(v)]:
                raise ValueError(f"variables are lowercase letters, digits and underscores: {bad}")
        return variables

    def default_instance_id(self) -> str:
        """``openmeteo-55_6050n-13_0038e`` for latitude 55.605, longitude 13.0038."""
        return f"{self.name}-{_coordinate(self.latitude, 'n', 's')}-{_coordinate(self.longitude, 'e', 'w')}"

    def check_model(self, channels: Collection[str]) -> None:
        """Raise ValueError when a chosen variable is not a channel of the model."""
        if self.variables and (missing := sorted(set(self.variables) - set(channels))):
            raise ValueError(f"the model {self.model_id} has no channel for {missing}; the platform would drop them")


class Entsoe(Source):
    """Day-ahead electricity prices in a bidding zone, from the ENTSO-E Transparency Platform.

    In EUR/MWh, at ENTSO-E's resolution (15 minutes or an hour). `bidding_zone` is a zone's short
    name, such as ``SE4`` (Sweden's are ``SE1`` to ``SE4``). The provider knows the Nordic, Baltic
    and several central European zones, and reports any other as the instance's configuration
    error.
    """

    name: ClassVar[str] = "entsoe"
    model_id: ClassVar[str] = "dtmi:apiproviders_entsoe;1.1"
    identity: ClassVar[tuple[str, ...]] = ("biddingZone", "dataType")

    bidding_zone: str
    data_type: Literal["dayAheadPrices"] = "dayAheadPrices"

    @field_validator("bidding_zone")
    @classmethod
    def _zone(cls, zone: str) -> str:
        zone = zone.strip().upper()
        if not _BIDDING_ZONE.match(zone):
            raise ValueError(f"bidding_zone is a zone's short name, such as SE4, not {zone!r}")
        return zone

    def default_instance_id(self) -> str:
        """``entsoe-se4-dayaheadprices`` for bidding zone SE4."""
        return f"{self.name}-{self.bidding_zone.lower()}-{self.data_type.lower()}"


class Riksbank(Source):
    """The Riksbank's daily exchange rates against SEK.

    One instance per environment: the currencies are the environment's setting of the source,
    not the instance's, so the source has no identity settings.
    """

    name: ClassVar[str] = "riksbank"
    model_id: ClassVar[str] = "dtmi:apiproviders_riksbank;1.1"
