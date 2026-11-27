"""Which API provider sources the environment has switched on, and their instances. Reads only."""

from _common import connect

from beacontower.apiproviders import ApiProvidersClient

providers = ApiProvidersClient(connect())
status = providers.status()
if not status.registered:
    raise SystemExit("This environment has no API provider registered.")

print(f"API provider {status.version or '(version unknown)'}, last report {status.reported_at}")
for source in status.sources:
    print(f"  {source.name}: {source.state}  {source.detail or ''}  devices {source.devices}")

for instance in providers.instances():
    print(f"{instance.id}  {instance.source}  {instance.health}  last success {instance.last_success_at}")
