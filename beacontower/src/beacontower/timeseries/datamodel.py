"""Data model expressed in pydantic."""

from pydantic import BaseModel


class TimeseriesCredentials(BaseModel):
    """Credentials used for authenticating to a psql timescale instance."""

    host: str
    login: str
    password: str
    port: int = 5432
    database: str = "timescale"
    table: str = "asset_timeseries"
