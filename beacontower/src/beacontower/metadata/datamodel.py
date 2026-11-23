"""Data model for the Beacon Tower metadata / core API, expressed in pydantic.

Where each model comes from (see `client.BeaconTowerClient`):

    Provider               GET  providers/            get_providers / get_provider
    Organization           GET  organizations         get_organizations
    Asset                  GET  assets/               get_assets / get_asset
    Model                  GET  models/               get_models / get_model
    Interface (DTDL)       GET  models/{id}/dtdl       get_dtdl_model  (also Model.model_data)
    Relation               POST relations/find         find_relations
    EnrollmentGroup        GET  enrollment-groups     get_enrollment_groups
    EnrollmentGroupDevice  GET  enrollment-groups/{id}/devices  get_enrollment_group_devices
    Device                 GET  devices               get_devices
    GraphNode/Edge/Tree    GET  graph/...             get_graph_*

Cross-links: Asset.model_id -> Model.id (a DTMI); Device.asset_id -> Asset.id;
Device/EnrollmentGroup.model_id -> Model.id; Relation ties an org/tenant/user
subject to a resource.

Quirks worth remembering:
  * `devices` and `enrollment-groups` paginate as {"items", "continuationToken"};
    the client follows the token via `?cursor=`. Other list endpoints return bare
    arrays. (If those ever start paginating they'll fail validation loudly, not
    truncate silently.)
  * `relations/find` needs `resourceType` in the body to return asset/model/org
    relations (default is users), and ignores the body's `subjectId` (returns many
    subjects) -- the client narrows client-side. Relations are owner|viewer|member|
    manager|*_privilegee.
  * Some onboarding timestamps use a 0001-01-01 "never" sentinel instead of null
    (normalised to None via OptionalTimestamp).
"""

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, TypeAdapter, model_validator

#: Co-type added by the Beacon Tower metaProperty extension.
META_PROPERTY = "MetaProperty"


def _listify(value: Any) -> Any:
    """DTDL allows a single string where an array is also valid."""
    return [value] if isinstance(value, str) else value


DtdlTypes = Annotated[list[str], BeforeValidator(_listify)]


def _none_if_min_datetime(value: Any) -> Any:
    """Onboarding endpoints sometimes use a 0001-01-01 'never' sentinel instead of
    null (e.g. an unprovisioned device's provisionedAt). Treat it as None."""
    if isinstance(value, str) and value.startswith("0001-01-01"):
        return None
    return value


#: A nullable timestamp that also maps the API's 0001-01-01 'never' sentinel to None.
OptionalTimestamp = Annotated[datetime | None, BeforeValidator(_none_if_min_datetime)]


class Provider(BaseModel):
    """A data provider (integration source), from GET providers/.

    `type` is the provider kind (e.g. the integration technology). Filtering is not
    supported by the endpoint yet, so get_providers returns them all.
    """

    id: str
    type: str
    display_name: str = Field(alias="displayName")
    description: str | None = None


_provider_list: TypeAdapter[list[Provider]] = TypeAdapter(list[Provider])


class Organization(BaseModel):
    """An organization, from GET organizations.

    Which models/assets an organization can see is found via find_relations (owner
    or viewer relations), NOT from this object. `parent_organization_id` is a
    hierarchy that is NOT represented in the relations graph -- if inherited
    visibility is ever needed, walk this field separately. `privileges` are the
    tenant-level capabilities granted to the org.
    """

    id: str
    display_name: str = Field(alias="displayName")
    description: str | None = None
    parent_organization_id: str | None = Field(default=None, alias="parentOrganizationId")
    contacts: list[Any] | None = None  # element shape unconfirmed (null in samples)
    tags: list[Any] | None = None  # element shape unconfirmed (null in samples)
    privileges: list[str] = Field(default_factory=list)


_organization_list: TypeAdapter[list[Organization]] = TypeAdapter(list[Organization])


class Asset(BaseModel):
    """A concrete asset (a device instance / twin), from GET assets/.

    `model_id` -> Model.id (a DTMI), the model this asset implements. In practice
    `id` matches the onboarded Device.device_id / asset_id, which is how devices
    link to assets. `authorization_context` is often null in samples. The endpoint
    is scoped to what the caller's token can see; pass find_relations output to
    get_assets to narrow further to a specific organization.
    """

    id: str
    model_id: str = Field(alias="modelId")
    display_name: str = Field(alias="displayName")
    description: str | None = None
    authorization_context: str | None = Field(default=None, alias="authorizationContext")
    # binding_data: dict[str, list[str]] = Field(alias="bindingData")# noqa: ERA001


_asset_list: TypeAdapter[list[Asset]] = TypeAdapter(list[Asset])


class Content(BaseModel):
    """Fields shared by every DTDL Content, the members of an Interface."""

    model_config = ConfigDict(populate_by_name=True)

    id: str | None = Field(default=None, alias="@id")
    types: DtdlTypes = Field(alias="@type")
    name: str
    display_name: str | None = Field(default=None, alias="displayName")
    description: str | None = None

    @property
    def quantity(self) -> str | None:
        """Semantic co-type from quantitativeTypes, e.g. Temperature. None if plain."""
        return next((t for t in self.types[1:] if t != META_PROPERTY), None)


class Telemetry(Content):
    """A value reported by the asset."""

    data_type: str = Field(alias="schema")
    unit: str | None = None


class Property(Content):
    """A value on the asset, optionally writable."""

    data_type: str = Field(alias="schema")
    unit: str | None = None
    writable: bool = False

    @property
    def is_meta(self) -> bool:
        """Whether this is a Beacon Tower MetaProperty rather than a device value."""
        return META_PROPERTY in self.types


class Command(Content):
    """An action that can be invoked on the asset."""


class Interface(BaseModel):
    """A DTDL v3 interface, with the wire `contents` array split by kind."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="@id")
    context: DtdlTypes = Field(alias="@context")
    types: DtdlTypes = Field(alias="@type")
    display_name: str | None = Field(default=None, alias="displayName")
    description: str | None = None
    telemetry: dict[str, Telemetry] = Field(default_factory=dict)
    properties: dict[str, Property] = Field(default_factory=dict)
    commands: dict[str, Command] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _split_contents(cls, data: Any) -> Any:
        """Partition the wire `contents` array into telemetry, properties and commands."""
        if not isinstance(data, dict) or "contents" not in data:
            return data

        data = dict(data)
        buckets: dict[str, dict[str, Any]] = {"Telemetry": {}, "Property": {}, "Command": {}}
        for entry in data.pop("contents") or ():
            kind = _listify(entry.get("@type"))[0]
            if kind not in buckets:
                msg = f"unsupported DTDL content type {kind!r} in {entry.get('@id')}"
                raise ValueError(msg)
            buckets[kind][entry["name"]] = entry

        data.setdefault("telemetry", buckets["Telemetry"])
        data.setdefault("properties", buckets["Property"])
        data.setdefault("commands", buckets["Command"])
        return data


class Model(BaseModel):
    """A model definition, from GET models/.

    `id` is a DTMI carrying a version, e.g. "dtmi:jemac;4.4" (major.minor); model
    versions are not unique across models, so callers may select specific ones.
    `model_type` is e.g. "asset". `model_data` holds the DTDL interface(s) (also
    fetchable alone via get_dtdl_model -> Interface). `draft` marks unpublished
    models. get_models accepts find_relations output to scope to an organization.
    """

    id: str
    display_name: str = Field(alias="displayName")
    model_data: list[Interface] = Field(alias="modelData")
    model_type: str = Field(alias="modelType")
    dtdl_model_id: str = Field(alias="dtdlModelId")
    dashboard_id: str | None = Field(default=None, alias="dashboardId")
    draft: bool

    @property
    def interface(self) -> Interface | None:
        """The sole interface, which is all the endpoint returns today."""
        return self.model_data[0] if self.model_data else None


_model_list: TypeAdapter[list[Model]] = TypeAdapter(list[Model])


class Relation(BaseModel):
    """A relation tuple from POST relations/find: a subject relates to a resource
    via a named relation, e.g. (organization) --owner--> (asset).

    Observed edges: tenant/user --owner|manager|member--> organization;
    organization --owner|viewer--> asset|model|user; organization
    --*_privilegee--> tenant. There are NO organization->organization edges (the
    org hierarchy lives on Organization.parent_organization_id instead).

    Endpoint quirks (handled in find_relations): the body needs `resourceType` to
    return asset/model/organization relations (default returns users), and the
    body's `subjectId` is ignored server-side (the response spans many subjects),
    so results are narrowed to the requested subject client-side. owner and viewer
    both mean "can see" the resource.
    """

    model_config = ConfigDict(populate_by_name=True)

    subject_type: str = Field(alias="subjectType")  # organization | tenant | user
    subject_id: str = Field(alias="subjectId")
    relation: str  # owner | viewer | member | manager | *_privilegee
    resource_type: str = Field(alias="resourceType")  # asset | model | user | organization | tenant
    resource_id: str = Field(alias="resourceId")


_relation_list: TypeAdapter[list[Relation]] = TypeAdapter(list[Relation])


class EnrollmentGroup(BaseModel):
    """An enrollment group; its devices are at /enrollment-groups/{id}/devices.

    Carries the organization and model it provisions devices into, and a
    `device_count`. Provisioning/credential/workflow fields on the wire are
    ignored (not needed here).
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str | None = None
    organization_id: str | None = Field(default=None, alias="organizationId")
    model_id: str | None = Field(default=None, alias="modelId")
    device_count: int | None = Field(default=None, alias="deviceCount")
    status: str | None = None
    attestation_type: str | None = Field(default=None, alias="attestationType")
    created_at: datetime | None = Field(default=None, alias="createdAt")


_enrollment_group_list: TypeAdapter[list[EnrollmentGroup]] = TypeAdapter(list[EnrollmentGroup])


class Device(BaseModel):
    """A device from the onboarding service, linked to an asset via `asset_id`.

    Lifecycle timestamps: `created_at` = added to the system, `provisioned_at` =
    a customer activated it, `configured_at` = it downloaded its config and is
    fully running. `last_seen_at` is only when the onboarding service last saw it
    -- NOT an uptime signal.
    """

    model_config = ConfigDict(populate_by_name=True)

    device_id: str = Field(alias="deviceId")
    asset_id: str | None = Field(default=None, alias="assetId")
    client_id: str | None = Field(default=None, alias="clientId")
    enrollment_group_id: str | None = Field(default=None, alias="enrollmentGroupId")
    provider_client_id: str | None = Field(default=None, alias="providerClientId")
    attestation_type: str | None = Field(default=None, alias="attestationType")
    provisioned: bool = False
    connectivity: Any = None
    metadata: dict[str, Any] | None = None
    last_activation_failure: Any = Field(default=None, alias="lastActivationFailure")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")
    provisioned_at: OptionalTimestamp = Field(default=None, alias="provisionedAt")
    configured_at: OptionalTimestamp = Field(default=None, alias="configuredAt")
    last_seen_at: OptionalTimestamp = Field(default=None, alias="lastSeenAt")
    last_refreshed_at: OptionalTimestamp = Field(default=None, alias="lastRefreshedAt")
    deleted_at: OptionalTimestamp = Field(default=None, alias="deletedAt")

    @property
    def is_provisioned(self) -> bool:
        """A customer has activated the device (`provisioned_at` is set)."""
        return self.provisioned_at is not None

    @property
    def is_configured(self) -> bool:
        """The device has downloaded its config and is fully running."""
        return self.configured_at is not None


_device_list: TypeAdapter[list[Device]] = TypeAdapter(list[Device])


class EnrollmentGroupDevice(BaseModel):
    """Membership of a device in an enrollment group, from
    GET /enrollment-groups/{id}/devices.

    A sparse projection -- only the device id, its group, and when it was
    provisioned. The full record with the asset link and lifecycle timestamps is
    GET /devices -> `Device`. `provisioned_at` is None until provisioned (the API
    returns a 0001-01-01 sentinel there, normalised to None).
    """

    model_config = ConfigDict(populate_by_name=True)

    device_id: str = Field(alias="deviceId")
    enrollment_group_id: str | None = Field(default=None, alias="enrollmentGroupId")
    provisioned_at: OptionalTimestamp = Field(default=None, alias="provisionedAt")

    @property
    def is_provisioned(self) -> bool:
        """A customer has activated the device (`provisioned_at` is set)."""
        return self.provisioned_at is not None


_enrollment_group_device_list: TypeAdapter[list[EnrollmentGroupDevice]] = TypeAdapter(list[EnrollmentGroupDevice])


class GraphNode(BaseModel):
    """A node in the asset/topology graph. The traversable start nodes come from
    GET graph/trees/; a single node from GET graph/nodes/{id}. Distinct from the
    authorization Relation graph above -- this is the domain topology."""

    id: str
    display_name: str = Field(alias="displayName")
    description: str | None = None


_graph_node_list: TypeAdapter[list[GraphNode]] = TypeAdapter(list[GraphNode])


class GraphEdge(BaseModel):
    """A directed edge between two graph nodes (embedded in a GraphTree), naming
    the relation and the from/to node types and ids."""

    id: str
    from_type: str = Field(alias="fromType")
    from_id: str = Field(alias="fromId")
    relation: str
    to_type: str = Field(alias="toType")
    to_id: str = Field(alias="toId")


class GraphTree(BaseModel):
    """A tree rooted at a start node, from GET graph/trees/{start_node_id}. Each
    child carries the `edge` that connects it to its parent; `children` recurses."""

    id: str
    display_name: str = Field(alias="displayName")
    description: str | None = None
    type: str
    children: "list[GraphTree] | None" = None
    edge: GraphEdge | None = None


GraphTree.model_rebuild()  # Self-Recursive reference requires this to be triggered
