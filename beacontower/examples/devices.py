"""Devices, the enrollment groups they join through, and a group's devices."""

from _common import connect

bt = connect()
devices = bt.get_devices(max_count=10)  # max_count stops paging early; .truncated says if more were left
print(f"{len(devices)} devices{' (more not fetched)' if devices.truncated else ''}")
for device in devices:
    print(f"  {device.device_id}  asset {device.asset_id}  provider client {device.provider_client_id}")

groups = bt.get_enrollment_groups(max_count=10)
print(f"\n{len(groups)} enrollment groups")
for group in groups:
    print(f"  {group.id}  {group.name}  model {group.model_id}  {group.device_count} devices")
if groups:
    for device in bt.get_enrollment_group_devices(groups[0].id, max_count=5):
        print(f"    in {groups[0].name}: {device.device_id}")
