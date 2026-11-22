"""Client for interacting with Beacon Tower."""

import re
import ssl
import warnings
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

import httpx

from beacontower.metadata.datamodel import (
    Asset,
    Device,
    EnrollmentGroup,
    EnrollmentGroupDevice,
    GraphNode,
    GraphTree,
    Interface,
    Model,
    Organization,
    Provider,
    Relation,
    _asset_list,
    _device_list,
    _enrollment_group_device_list,
    _enrollment_group_list,
    _graph_node_list,
    _model_list,
    _organization_list,
    _provider_list,
    _relation_list,
)
from beacontower.metadata.pagination import PaginatedList

_KNOWN_CLIENT_VERSIONS = ("0.20.7", "0.30.0")
Verify = bool | str | ssl.SSLContext


def _ssl_verify(verify: Verify) -> bool | ssl.SSLContext:
    """Return what httpx takes as `verify`: a path to a CA bundle becomes an SSL context."""
    return ssl.create_default_context(cafile=verify) if isinstance(verify, str) else verify


def _identify_gateway(url: str, timeout: float = 10.0, verify: Verify = True) -> str:
    """
    Return the gateway base URL.

    A URL that already has a path is taken as the API's own address; a bare ``/api`` is
    completed to ``/api/v1``, where the gateway serves it. For a bare host the client
    version is read from index.html (or from the /version.js it loads), then coreApi.url
    from the /envConfig.<version>.js sitting alongside it.
    """
    url = (url if "://" in url else f"https://{url}").rstrip("/")
    if path := urlparse(url).path:
        return f"{url}/v1/" if path == "/api" else f"{url}/"

    with httpx.Client(base_url=url, timeout=timeout, follow_redirects=True, verify=_ssl_verify(verify)) as client:
        index = client.get("/").raise_for_status().text
        if match := re.search(r"/version\.(\d[\d.]*)\.js", index):
            version = match.group(1)
        elif re.search(r"""src=["']/version\.js["']""", index):
            published = re.search(r"""clientVersion\s*=\s*['"]([^'"]+)""", client.get("/version.js").raise_for_status().text)
            if not published:
                raise RuntimeError(f"{url} does not publish its client version")
            version = published.group(1)
        else:
            raise RuntimeError(f"{url} is not a Beacon Tower frontend")
        if (release := re.match(r"[\d.]+", version)) is None or release.group().rstrip(".") not in _KNOWN_CLIENT_VERSIONS:
            warnings.warn(f"{url} runs client {version}, not one of {_KNOWN_CLIENT_VERSIONS}", stacklevel=2)
        config = client.get(f"/envConfig.{version}.js").raise_for_status().text

    found = re.search(r"""coreApi[^{]*\{[^}]*?url['"]?\s*:\s*['"]([^'"]+)""", config)
    if not found:
        raise RuntimeError(f"no coreApi URL published by {url}")
    return f"{found[1].rstrip('/')}/"


class BeaconTowerClient:
    """Beacon Tower Client."""

    _authentication_suffix = "auth/ropc"
    _organizations_suffix = "organizations"
    _devices_suffix = "devices"
    _enrollment_groups_suffix = "enrollment-groups"
    _providers_suffix = "providers/"
    _asset_suffix = "assets/"
    _model_suffix = "models/"
    _graph_start_nodes_suffix = "graph/trees/"
    _graph_node_suffix = "graph/nodes/"
    _graph_tree_suffix = "graph/trees/"
    # NB: assumes the resolved coreApi base already includes the /api/v1/ prefix,
    # consistent with the other bare suffixes. If not, prepend it here.
    _relations_find_suffix = "relations/find"
    # The container resource type recursed through by find_relations(recursive=True).
    # Only "organization" -- deliberately not "tenant", since org --privilegee-->
    # tenant --owner--> every sibling org would leak unrelated organizations.
    _relation_traverse_type = "organization"

    def __init__(  # noqa: PLR0913
        self,
        url: str,
        user_name: str,
        password: str,
        resolve_url: bool = True,
        timeout: float | None = None,
        *,
        verify: Verify = True,
    ) -> None:
        """Create a client for the Beacon Tower at `url`, logging in with a user name and password.

        `url` is the address of the web client, from which the API's address is read, or
        the API's own address (``https://<host>/api/v1``; always, when `resolve_url` is
        False). `timeout` is the seconds one request may take; without it httpx's default
        applies. `verify` is httpx's: False to skip checking the server's certificate, or
        the path of a CA bundle (or an ``ssl.SSLContext``) for a deployment signed by its
        own authority.
        """
        self.url = url
        self._request_options: dict[str, Any] = {"verify": _ssl_verify(verify)}
        if timeout is not None:
            self._request_options["timeout"] = timeout
        if resolve_url:
            self.url = _identify_gateway(self.url, verify=self._request_options["verify"])
        self.url = self.url if self.url.endswith("/") else self.url + "/"
        self.user_name = user_name
        self.password = password

        self.header: dict[str, str] | None = None
        self._logged_in = False
        self.client = httpx.Client(verify=self._request_options["verify"])

    def _get_headers(self) -> dict[str, str]:
        if self.header is None:
            request = httpx.post(
                self.url + self._authentication_suffix,
                json={"username": self.user_name, "password": self.password},
                **self._request_options,
            )
            request.raise_for_status()
            token = request.json()["accessToken"]
            self.header = {"Authorization": f"Bearer {token}"}
            self._logged_in = True
        return self.header

    def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Send an authenticated request and raise on any non-2xx status.

        A token this client fetched itself is fetched again, once, when the API answers
        401, so a client that outlives its token keeps working. A header set by hand is
        left alone.
        """
        # Looked up at each call (httpx.get, httpx.post, ...), so a test can replace one.
        send = getattr(httpx, method.lower())
        response: httpx.Response = send(self.url + path, headers=self._get_headers(), **self._request_options, **kwargs)
        if response.status_code == httpx.codes.UNAUTHORIZED and self._logged_in:
            self.header = None
            response = send(self.url + path, headers=self._get_headers(), **self._request_options, **kwargs)
        response.raise_for_status()
        return response

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Mapping[str, object] | None = None,
        params: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        """Send an authenticated request to the API; return the response, raise on a non-2xx status.

        For endpoints this client has no method for. `method` is GET, POST, PUT, PATCH or
        DELETE; `path` is relative to the API's address, such as ``providers/x/clients``. The
        login, and the retry after an expired token, are those of every other call.
        """
        method = method.upper()
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            raise ValueError(f"unsupported method {method}")
        kwargs: dict[str, Any] = {}
        if json is not None:
            if method in {"GET", "DELETE"}:
                raise ValueError(f"a {method} request has no body")
            kwargs["json"] = json
        if params is not None:
            kwargs["params"] = params
        return self._send(method, path.lstrip("/"), **kwargs)

    def check_auth(self) -> None:
        """Verify the endpoint is reachable and the credentials authenticate.

        Requests an access token (unless a header was set manually) and confirms it is
        accepted by a lightweight authenticated call. Raises RuntimeError with a clear
        message on failure -- a cheap way to sanity-check credentials from a config
        source before running real work.
        """
        if self.header is None:
            auth = httpx.post(
                self.url + self._authentication_suffix,
                json={"username": self.user_name, "password": self.password},
                **self._request_options,
            )
            token = auth.json().get("accessToken") if auth.status_code == httpx.codes.OK else None
            if not token:
                raise RuntimeError(
                    f"authentication failed at {self.url}{self._authentication_suffix} (status {auth.status_code}) "
                    "-- check the endpoint and username/password"
                )
            self.header = {"Authorization": f"Bearer {token}"}
            self._logged_in = True
        probe = httpx.get(self.url + self._organizations_suffix, headers=self.header, **self._request_options)
        if probe.status_code == httpx.codes.UNAUTHORIZED:
            raise RuntimeError(f"token rejected by {self.url} (401) -- endpoint/realm mismatch or expired credentials")
        probe.raise_for_status()

    def _get_json(self, path: str, *, params: Mapping[str, str] | None = None) -> Any:
        """GET ``path`` and return parsed JSON, raising on any non-2xx status.

        Centralises status-checking for the data endpoints: a 4xx/5xx -- e.g. a 403
        ``{"error": "forbidden"}`` from a missing privilege -- raises
        ``httpx.HTTPStatusError`` instead of being parsed as data. Without this, an
        error body (a dict with no ``items``) was silently coerced into an empty list,
        so a permission failure looked like "0 devices" to the caller.
        """
        return self._send("GET", path, params=params).json()

    def _post_json(self, path: str, *, json: Mapping[str, object]) -> Any:
        """POST ``json`` to ``path`` and return parsed JSON, raising on any non-2xx status."""
        return self._send("POST", path, json=json).json()

    def get_organizations(self) -> list[Organization]:
        """List all organizations."""
        res = self._get_json(self._organizations_suffix)
        organizations: list[Organization] = _organization_list.validate_python(res)
        return organizations

    def get_providers(self) -> list[Provider]:
        """List providers."""
        # TODO: filtering capability missing
        res = self._get_json(self._providers_suffix)
        providers: list[Provider] = _provider_list.validate_python(res)
        return providers

    def get_provider(self, provider_id: str) -> Provider:
        """Get a specific provider by id."""
        return Provider.model_validate(self._get_json(self._providers_suffix + provider_id))

    def _relations_query(self, subject_id: str, resource_type: str | None) -> list[Relation]:
        """One relations/find call, narrowed to `subject_id` (the endpoint returns
        relations across several subjects and ignores the body's subjectId)."""
        body: dict[str, str] = {"subjectId": subject_id}
        if resource_type is not None:
            body["resourceType"] = resource_type
        res = self._post_json(self._relations_find_suffix, json=body)
        relations = _relation_list.validate_python(res)
        return [relation for relation in relations if relation.subject_id == subject_id]

    def find_relations(
        self,
        subject_id: str,
        resource_type: str | None = None,
        recursive: bool = False,
    ) -> list[Relation]:
        """Find the relations for a subject (e.g. an organization id): the resources
        it owns or can view. Pass `resource_type` ("asset", "model", "user", ...) to
        restrict to that kind — needed to get asset/model relations. Feed the result
        to get_assets / get_models to scope them to that organization.

        Each relation is `subject_id` as owner/viewer of the resource. With
        `recursive=True`, the organization graph is walked from `subject_id` (a user
        or tenant reaches the organizations it belongs to, and their resources),
        following only `organization` memberships and never visiting a node twice.
        The result is flat and de-duplicated.
        """
        if not recursive:
            return self._relations_query(subject_id, resource_type)

        visited: set[str] = set()
        frontier: list[str] = [subject_id]
        collected: dict[tuple[str, str, str, str], Relation] = {}
        while frontier:
            node = frontier.pop()
            if node in visited:
                continue
            visited.add(node)
            for relation in self._relations_query(node, resource_type):
                collected[(relation.subject_id, relation.relation, relation.resource_type, relation.resource_id)] = relation
            frontier.extend(
                organization.resource_id
                for organization in self._relations_query(node, self._relation_traverse_type)
                if organization.resource_id not in visited
            )
        return list(collected.values())

    def get_assets(self, org_objects: list[Relation] | None = None) -> list[Asset]:
        """Get assets, optionally scoped to an organization.

        Pass the relations from `find_relations` as `org_objects` to keep only the
        assets that organization can see (filtered client-side on object id, since
        the endpoint has no server-side filtering yet).
        """
        res = self._get_json(self._asset_suffix)
        assets: list[Asset] = _asset_list.validate_python(res)
        if org_objects is None:
            return assets
        allowed = {relation.resource_id for relation in org_objects}
        return [asset for asset in assets if asset.id in allowed]

    def get_asset(self, asset_id: str) -> Asset:
        """Get a specific asset by id."""
        return Asset.model_validate(self._get_json(self._asset_suffix + asset_id))

    def get_models(self, org_objects: list[Relation] | None = None) -> list[Model]:
        """Get models, optionally scoped to an organization.

        Pass the relations from `find_relations` as `org_objects` to keep only the
        models that organization can see (filtered client-side on object id).
        """
        res = self._get_json(self._model_suffix)
        models: list[Model] = _model_list.validate_python(res)
        if org_objects is None:
            return models
        allowed = {relation.resource_id for relation in org_objects}
        return [model for model in models if model.id in allowed]

    def get_model(self, model_id: str) -> Model:
        """Get a specific model."""
        return Model.model_validate(self._get_json(self._model_suffix + model_id))

    def get_dtdl_model(self, model_id: str) -> Interface:
        """Get the DTDL interface for a specific model.

        The endpoint returns a JSON array (the requested interface plus any it
        depends on); pick the one whose `@id` matches `model_id`.
        """
        payload = self._get_json(self._model_suffix + model_id + "/dtdl")
        if isinstance(payload, list):
            payload = next((i for i in payload if i.get("@id") == model_id), payload[0])
        return Interface.model_validate(payload)

    def _get_paginated(self, path: str, max_count: int | None = None) -> PaginatedList:
        """GET pages of an `{"items": [...], "continuationToken": ...}` endpoint,
        following the token as ?cursor=. A bare-list response is one page. Stops once
        `max_count` items have been collected (if given); the result's `.truncated`
        reports whether more was available.
        """
        items: PaginatedList = PaginatedList()
        cursor: str | None = None
        while True:
            params = {"cursor": cursor} if cursor else {}
            payload = self._get_json(path, params=params)
            if isinstance(payload, dict):
                items.extend(payload.get("items", []))
                cursor = payload.get("continuationToken")
            else:
                items.extend(payload)
                cursor = None
            if max_count is not None and len(items) >= max_count:
                items.truncated = len(items) > max_count or bool(cursor)
                del items[max_count:]
                return items
            if not cursor:
                items.truncated = False
                return items

    def get_devices(self, max_count: int | None = None) -> PaginatedList[Device]:
        """List devices (following pagination); each links to an asset via `asset_id`.
        `max_count` caps how many are downloaded; the result's `.truncated` is True if
        more were available."""
        raw = self._get_paginated(self._devices_suffix, max_count)
        devices: PaginatedList[Device] = PaginatedList(_device_list.validate_python(raw))
        devices.truncated = raw.truncated
        return devices

    def get_enrollment_groups(self, max_count: int | None = None) -> PaginatedList[EnrollmentGroup]:
        """List enrollment groups (following pagination). `max_count` caps the count;
        the result's `.truncated` says whether more were available."""
        raw = self._get_paginated(self._enrollment_groups_suffix, max_count)
        groups: PaginatedList[EnrollmentGroup] = PaginatedList(_enrollment_group_list.validate_python(raw))
        groups.truncated = raw.truncated
        return groups

    def get_enrollment_group_devices(self, enrollment_group_id: str, max_count: int | None = None) -> PaginatedList[EnrollmentGroupDevice]:
        """List a group's device memberships (following pagination). Sparse projection
        (id + group + provisioning time); for the full device with its asset link use
        get_devices(). `max_count` caps the count; the result's `.truncated` says
        whether more were available."""
        path = f"{self._enrollment_groups_suffix}/{enrollment_group_id}/devices"
        raw = self._get_paginated(path, max_count)
        devices: PaginatedList[EnrollmentGroupDevice] = PaginatedList(_enrollment_group_device_list.validate_python(raw))
        devices.truncated = raw.truncated
        return devices

    def get_graph_list_start_nodes(self) -> list[GraphNode]:
        """List start nodes for graph."""
        res = self._get_json(self._graph_start_nodes_suffix)
        nodes: list[GraphNode] = _graph_node_list.validate_python(res)
        return nodes

    def get_graph_node(self, node_id: str) -> GraphNode:
        """Get a specific graph node."""
        return GraphNode.model_validate(self._get_json(self._graph_node_suffix + node_id))

    def get_graph_tree(self, start_node_id: str) -> GraphTree:
        """Get a graph tree."""
        return GraphTree.model_validate(self._get_json(self._graph_tree_suffix + start_node_id))
