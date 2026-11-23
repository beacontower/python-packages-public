"""Which data provider sources the environment has switched on, and their instances. Reads only."""

from _common import connect

from beacontower.dataproviders import DataProvidersClient

providers = DataProvidersClient(connect())
status = providers.status()
if not status.registered:
    raise SystemExit("This environment has no data provider registered.")

print(f"Data provider {status.version or '(version unknown)'}, last report {status.reported_at}")
for source in status.sources:
    print(f"  {source.name}: {source.state}  {source.detail or ''}  devices {source.devices}")

for instance in providers.instances():
    print(f"{instance.id}  {instance.source}  {instance.health}  last success {instance.last_success_at}")
