# python-packages-public

Public Python packages for [Beacon Tower](https://github.com/beacontower).

| Package | Install | What it is |
|---|---|---|
| [`beacontower`](beacontower/) | `pip install beacontower` | Client for a tenant's metadata (assets, models, devices, organizations), timeseries and data providers |

Every package here installs below the import name `beacontower` (`beacontower.metadata`,
`beacontower.timeseries`, `beacontower.dataproviders`). The name is a namespace shared between distributions, so a
package MUST NOT add a `beacontower/__init__.py`.
