"""The organizations the login can see, and the assets and models each one holds."""

from _common import connect

bt = connect()
for organization in bt.get_organizations():
    parent = f" (under {organization.parent_organization_id})" if organization.parent_organization_id else ""
    print(f"{organization.display_name}  {organization.id}{parent}")

    # Relations tie resources to an organization; get_assets and get_models take them
    # to scope a list to it.
    assets = bt.get_assets(bt.find_relations(organization.id, resource_type="asset"))
    models = bt.get_models(bt.find_relations(organization.id, resource_type="model"))
    print(f"  {len(assets)} assets, {len(models)} models")
