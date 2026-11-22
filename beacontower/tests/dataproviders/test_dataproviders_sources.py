"""The source classes: their tags, ids, validation, and reading them back from device tags."""

from typing import ClassVar

import pytest
from pydantic import ValidationError

from beacontower.dataproviders import Entsoe, OpenMeteo, OtherSource, Riksbank, Source, source_class, source_classes
from beacontower.dataproviders import sources as sources_module


def test_open_meteo_tags_leave_out_what_is_not_set() -> None:
    tags = OpenMeteo(latitude=55.605, longitude=13.0038, history_days=30).tags()
    assert tags == {"source": "openmeteo", "historyDays": 30, "latitude": 55.605, "longitude": 13.0038}


def test_open_meteo_variables_are_a_list_tag() -> None:
    tags = OpenMeteo(latitude=0, longitude=0, variables=("temperature_2m", "precipitation")).tags()
    assert tags["variables"] == ["temperature_2m", "precipitation"]


@pytest.mark.parametrize(
    "fields",
    [
        {"latitude": 91, "longitude": 0},
        {"latitude": 0, "longitude": -181},
        {"latitude": 0, "longitude": 0, "history_days": 3661},
        {"latitude": 0, "longitude": 0, "history_days": -1},
        {"latitude": 0, "longitude": 0, "variables": ()},
        {"latitude": 0, "longitude": 0, "variables": ("Temperature",)},
        {"lattitude": 0, "longitude": 0},
    ],
)
def test_open_meteo_refuses_bad_settings(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        OpenMeteo(**fields)  # type: ignore[arg-type]


def test_default_ids_hold_no_dot() -> None:
    assert OpenMeteo(latitude=55.605, longitude=13.0038).default_instance_id() == "openmeteo-55_6050n-13_0038e"
    assert OpenMeteo(latitude=-33.8688, longitude=-70.6693).default_instance_id() == "openmeteo-33_8688s-70_6693w"
    assert Entsoe(bidding_zone="se3").default_instance_id() == "entsoe-se3-dayaheadprices"
    assert Riksbank().default_instance_id() == "riksbank"


def test_entsoe_zone_is_upper_case_and_checked() -> None:
    assert Entsoe(bidding_zone=" se3 ").tags() == {"source": "entsoe", "historyDays": 0, "biddingZone": "SE3", "dataType": "dayAheadPrices"}
    with pytest.raises(ValidationError):
        Entsoe(bidding_zone="SE 3")


def test_identity_values_follow_the_identity_tags() -> None:
    assert OpenMeteo(latitude=1.5, longitude=2.5, history_days=9).identity_values() == (1.5, 2.5)
    assert Entsoe(bidding_zone="FI").identity_values() == ("FI", "dayAheadPrices")
    assert Riksbank(history_days=30).identity_values() == ()


def test_tags_are_read_back_into_the_source_class() -> None:
    tags = {"source": "openmeteo", "parser": "json", "latitude": 55.605, "longitude": 13.0038, "historyDays": 30}
    assert Source.from_tags(tags) == OpenMeteo(latitude=55.605, longitude=13.0038, history_days=30)


def test_an_unknown_source_is_read_as_other_source() -> None:
    read = Source.from_tags({"source": "nordpool", "area": "SE3"})
    assert read == OtherSource(source="nordpool", settings={"area": "SE3"})


def test_tags_the_class_cannot_read_are_kept_as_they_are() -> None:
    read = Source.from_tags({"source": "openmeteo", "latitude": 200, "longitude": 0})
    assert read == OtherSource(source="openmeteo", settings={"latitude": 200, "longitude": 0})


def test_a_new_source_registers_itself() -> None:
    class Weatherstation(Source):
        name: ClassVar[str] = "weatherstation"
        model_id: ClassVar[str] = "dtmi:dataproviders_weatherstation;1.1"
        identity: ClassVar[tuple[str, ...]] = ("stationId",)

        station_id: str

    try:
        assert source_class("weatherstation") is Weatherstation
        assert "weatherstation" in source_classes()
        read = Source.from_tags({"source": "weatherstation", "stationId": "52350"})
        assert read == Weatherstation(station_id="52350")
        assert read.tags() == {"source": "weatherstation", "historyDays": 0, "stationId": "52350"}
    finally:
        sources_module._SOURCES.pop("weatherstation")


def test_a_source_name_is_registered_once() -> None:
    with pytest.raises(TypeError, match="already registered"):

        class Again(Source):
            name: ClassVar[str] = "openmeteo"


def test_the_shipped_sources_are_registered() -> None:
    assert list(source_classes()) == ["entsoe", "openmeteo", "riksbank"]
