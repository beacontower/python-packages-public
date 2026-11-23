"""General database health queries."""

import pandas as pd
from psycopg import Cursor
from psycopg.sql import SQL, Identifier

_LIST_SIZE_OF_TELEMETRY = SQL(
    """SELECT COUNT(*) as cnt,
            FIRST(time,time) as first_time,
            LAST(time,time) as last_time,
            modelid,
            assetid,
            timeseries_name
        FROM {table}
            GROUP BY modelid,assetid, timeseries_name
        ORDER BY cnt ASC"""
)


def list_size_of_telemetry(cursor: Cursor, table_name: str) -> pd.DataFrame:
    """List size of timescale db series."""
    cursor.execute(_LIST_SIZE_OF_TELEMETRY.format(table=Identifier(table_name)))
    return pd.DataFrame(cursor.fetchall(), columns=[col.name for col in cursor.description or []])
