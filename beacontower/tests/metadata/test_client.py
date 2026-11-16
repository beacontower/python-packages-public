"""Status-handling regression tests for :class:`BeaconTowerClient`.

A data endpoint that returns an error status -- e.g. a 403 ``{"error": "forbidden"}``
from a missing privilege -- must raise, not have its error body silently parsed into an
empty result. That silent coercion is what made a permission failure look like
"0 devices" and let the KPI pipeline ship a zeroed report as if it were healthy.
"""

import httpx
import pytest

from beacontower.metadata.client import BeaconTowerClient


def _client() -> BeaconTowerClient:
    client = BeaconTowerClient("http://gateway/api/v1/", "user", "pw", resolve_url=False)
    client.header = {"Authorization": "Bearer test"}  # pre-set so no auth round-trip is attempted
    return client


def _response(status: int, body: object) -> httpx.Response:
    return httpx.Response(status, json=body, request=httpx.Request("GET", "http://gateway/"))


def test_paginated_endpoint_raises_on_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _response(403, {"error": "forbidden", "message": "Not permitted"}))
    with pytest.raises(httpx.HTTPStatusError):
        _client().get_devices()


def test_list_endpoint_raises_on_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _response(403, {"error": "forbidden"}))
    with pytest.raises(httpx.HTTPStatusError):
        _client().get_organizations()


def test_server_error_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _response(500, {"error": "boom"}))
    with pytest.raises(httpx.HTTPStatusError):
        _client().get_assets()


def test_genuine_empty_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    # A real 200 with no items is legitimate (an org with no devices) and must NOT raise.
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _response(200, {"items": [], "continuationToken": None}))
    assert list(_client().get_devices()) == []


def test_an_expired_token_is_fetched_again_once(monkeypatch: pytest.MonkeyPatch) -> None:
    client = BeaconTowerClient("http://gateway/api/v1/", "user", "pw", resolve_url=False)
    logins: list[int] = []
    answers = [_response(401, {}), _response(200, [])]

    def login(*a: object, **k: object) -> httpx.Response:
        logins.append(1)
        return _response(200, {"accessToken": f"t{len(logins)}"})

    monkeypatch.setattr(httpx, "post", login)
    monkeypatch.setattr(httpx, "get", lambda *a, **k: answers.pop(0))
    assert client.get_organizations() == []
    assert len(logins) == 2
    assert client.header == {"Authorization": "Bearer t2"}


def test_a_header_set_by_hand_is_not_replaced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _response(401, {}))
    with pytest.raises(httpx.HTTPStatusError):
        _client().get_organizations()
