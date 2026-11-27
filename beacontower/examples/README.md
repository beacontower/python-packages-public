# Examples

One short script per operation of the `beacontower` package. Each finds its data among
what the login can see: the first organization, asset or signal with readings. You only
set the address and an account without MFA (see "Logging in" in the package README):

```
pip install beacontower
export BEACONTOWER_URL=https://<your-beacon-tower>   # the address you open in a browser
export BEACONTOWER_USER=...
export BEACONTOWER_PASSWORD=...
python examples/login.py
```

`BEACONTOWER_VERIFY=false` accepts a self-signed certificate, as on a local test
environment. `_common.py` holds the login and the search for a signal with readings, which
the timeseries examples share.

Every script reads only, apart from `api_provider_instance.py`. It makes an instance, reads
it and removes it, and only when it is run with `--yes`. `from_postgres.py` also needs the
database's address and login (`BEACONTOWER_PG_HOST`, `BEACONTOWER_PG_LOGIN`,
`BEACONTOWER_PG_PASSWORD`) and `pip install "beacontower[postgres]"`.
