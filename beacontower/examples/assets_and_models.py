"""Assets, the model of one, and the signals that model says the asset reports."""

from _common import connect

bt = connect()
assets = bt.get_assets()
print(f"{len(assets)} assets")
for asset in assets[:10]:
    print(f"  {asset.id}  {asset.display_name}  model {asset.model_id}")

if assets:
    asset = bt.get_asset(assets[0].id)
    model = bt.get_model(asset.model_id)
    print(f"\n{asset.display_name} is a {model.display_name} ({model.id})")
    for interface in model.model_data:  # the model's DTDL; get_dtdl_model(model.id) gives the same
        for name, telemetry in interface.telemetry.items():
            print(f"  telemetry {name}: {telemetry.data_type}, {telemetry.unit or 'no unit'}  {telemetry.display_name or ''}")
        for name in interface.properties:
            print(f"  property  {name}")
