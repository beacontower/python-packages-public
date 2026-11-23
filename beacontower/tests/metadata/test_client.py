"""Status-handling regression tests for :class:`BeaconTowerClient`.

A data endpoint that returns an error status -- e.g. a 403 ``{"error": "forbidden"}``
from a missing privilege -- must raise, not have its error body silently parsed into an
empty result. That silent coercion is what made a permission failure look like
"0 devices" and let the KPI pipeline ship a zeroed report as if it were healthy.
"""

import httpx
import pytest

from beacontower.metadata.client import BeaconTowerClient, _identify_gateway


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


_ENV_CONFIG = "window.envConfig = {\n  coreApi: {\n    url: 'https://tower.example/api/v1'\n  }\n};"


def _frontend(monkeypatch: pytest.MonkeyPatch, pages: dict[str, str]) -> None:
    """Serve `pages` (path -> body) to the httpx.Client that URL resolution opens; any other path is a 404."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = pages.get(request.url.path)
        return httpx.Response(404) if body is None else httpx.Response(200, text=body)

    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(base_url=kwargs["base_url"], transport=httpx.MockTransport(handler)))


def test_bare_host_resolves_through_a_versioned_script(monkeypatch: pytest.MonkeyPatch) -> None:
    _frontend(monkeypatch, {"/": '<script src="/version.0.20.7.js"></script>', "/envConfig.0.20.7.js": _ENV_CONFIG})
    assert _identify_gateway("tower.example") == "https://tower.example/api/v1/"


def test_bare_host_resolves_through_version_js(monkeypatch: pytest.MonkeyPatch) -> None:
    version = "0.30.0-prod.33@sha256:1cbe"
    _frontend(
        monkeypatch,
        {
            "/": '<!-- <script src="/envConfig.js"></script> -->\n<script type="module" src="/version.js"></script>',
            "/version.js": f"window.clientVersion = '{version}';\nwindow.appVersion = 'unknown';",
            f"/envConfig.{version}.js": _ENV_CONFIG,
        },
    )
    assert _identify_gateway("https://tower.example/") == "https://tower.example/api/v1/"


def test_a_page_that_is_no_frontend_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _frontend(monkeypatch, {"/": "<html>hello</html>"})
    with pytest.raises(RuntimeError, match="not a Beacon Tower frontend"):
        _identify_gateway("tower.example")


@pytest.mark.parametrize(
    ("given", "resolved"),
    [
        ("https://tower.example/api", "https://tower.example/api/v1/"),
        ("https://tower.example/api/", "https://tower.example/api/v1/"),
        ("https://tower.example/api/v1", "https://tower.example/api/v1/"),
        ("https://tower.example/gateway/api", "https://tower.example/gateway/api/"),
    ],
)
def test_an_api_address_is_not_looked_up(given: str, resolved: str) -> None:
    assert _identify_gateway(given) == resolved


def test_verify_reaches_every_request(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[object] = []

    def post(*_: object, **kwargs: object) -> httpx.Response:
        seen.append(kwargs["verify"])
        return _response(200, {"accessToken": "t"})

    def get(*_: object, **kwargs: object) -> httpx.Response:
        seen.append(kwargs["verify"])
        return _response(200, {"items": [], "continuationToken": None})

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(httpx, "get", get)
    BeaconTowerClient("http://gateway/api/v1/", "user", "pw", resolve_url=False, verify=False).check_auth()
    assert seen == [False, False]
