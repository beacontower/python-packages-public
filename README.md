# python-packages-public

[![PyPI](https://img.shields.io/pypi/v/beacontower)](https://pypi.org/project/beacontower/)
[![Python](https://img.shields.io/pypi/pyversions/beacontower)](https://pypi.org/project/beacontower/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](https://github.com/beacontower/python-packages-public/blob/main/beacontower/LICENSE)
[![Publish](https://github.com/beacontower/python-packages-public/actions/workflows/publish.yml/badge.svg?branch=main)](https://github.com/beacontower/python-packages-public/actions/workflows/publish.yml)
[![Coverage](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/beacontower/python-packages-public/badges/coverage.json)](https://github.com/beacontower/python-packages-public/actions/workflows/publish.yml)
[![mypy: strict](https://img.shields.io/badge/mypy-strict-blue)](https://mypy.readthedocs.io/en/stable/command_line.html#cmdoption-mypy-strict)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

Public Python packages for [Beacon Tower](https://github.com/beacontower).

| Package | Install | What it is |
|---|---|---|
| [`beacontower`](beacontower/) | `pip install beacontower` | Client for a tenant's metadata (assets, models, devices, organizations), timeseries and API providers |

Every package here installs below the import name `beacontower` (`beacontower.metadata`,
`beacontower.timeseries`, `beacontower.apiproviders`). The name is a namespace shared between distributions, so a
package MUST NOT add a `beacontower/__init__.py`.

Each package is generated from the Beacon Tower Python package template, and its `.cruft.json` records the
template commit it was made from. `cruft check` shows whether the template has moved on, and `cruft update`
brings those changes in.
