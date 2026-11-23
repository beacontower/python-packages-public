"""Utility functions to transform data (typically raw data)."""

import typing
from datetime import datetime, timedelta

import pandas as pd


def unpivot_data_dump(df: pd.DataFrame) -> pd.DataFrame:
    """Pivot a long telemetry dump into a wide, time-indexed frame.

    Takes a dump with `timeseries_name`, `time` and `value`/`stringvalue` columns and
    returns a DataFrame indexed by time with one column per timeseries, numeric and
    string series merged on the shared time index.
    """
    string_df = df.loc[df["type"] == "stringtype"].copy()
    numeric_df = df.loc[~(df["type"] == "stringtype")].copy()
    if len(numeric_df) > 0:
        numeric_df = (
            numeric_df[["timeseries_name", "time", "value"]].pivot_table(index=["timeseries_name"], columns=["time"]).T.droplevel(0)
        )
    else:
        numeric_df = pd.DataFrame(index=pd.DatetimeIndex(data=[], tz="UTC"))
    if len(string_df) > 0:
        string_df = (
            string_df[["timeseries_name", "time", "stringvalue"]].pivot_table(index=["timeseries_name"], columns=["time"]).T.droplevel(0)
        )
    else:
        string_df = pd.DataFrame(index=pd.DatetimeIndex(data=[], tz="UTC"))
    return numeric_df.merge(string_df, how="outer", left_index=True, right_index=True)


def _prepare_start_end_time(
    *,
    df: pd.DataFrame,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    precise_time_hour_start_end: bool = True,
) -> tuple[datetime, datetime]:
    if start_time is None:
        start_time_actual = typing.cast("datetime", df.index[0])

        if precise_time_hour_start_end:
            tmp = datetime(
                year=start_time_actual.year,
                month=start_time_actual.month,
                day=start_time_actual.day,
                hour=start_time_actual.hour,
                tzinfo=start_time_actual.tzinfo,
            )
            if (start_time_actual.second != 0) | (start_time_actual.microsecond != 0):
                start_time_actual = tmp + timedelta(hours=1)
    else:
        start_time_actual = start_time
    if end_time is None:
        end_time_actual = typing.cast("datetime", df.index[-1])

        if precise_time_hour_start_end:
            tmp = datetime(
                year=end_time_actual.year,
                month=end_time_actual.month,
                day=end_time_actual.day,
                hour=end_time_actual.hour,
                tzinfo=end_time_actual.tzinfo,
            )
            if (end_time_actual.second != 0) | (end_time_actual.microsecond != 0):
                end_time_actual = tmp + timedelta(hours=1)
    else:
        end_time_actual = end_time

    return start_time_actual, end_time_actual


def transform_data_forward_fill(  # noqa: PLR0913, PLR0917
    df: pd.DataFrame,
    time_resolution: timedelta = timedelta(hours=1),
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    default_none_to_zero_if_numeric: bool = False,
    precise_time_hour_start_end: bool = True,
    interpolate_values: bool = False,
) -> pd.DataFrame:
    """Resample a wide, UTC-indexed frame onto a regular grid, carrying values forward.

    `df` is the pivoted frame (time in UTC). The output is reindexed to a regular grid
    of `time_resolution` steps between `start_time` and `end_time` (both inferred from
    the data when omitted); values are forward-filled, or linearly interpolated in time
    when `interpolate_values` is set. When `precise_time_hour_start_end` is set the grid
    snaps to the resolution (e.g. hourly grids land on XX:00:00) rather than the first
    timestamp. With `default_none_to_zero_if_numeric`, leading gaps in numeric columns
    (before any real value) are filled with 0 instead of left NaN.
    """
    start_time, end_time = _prepare_start_end_time(
        df=df, start_time=start_time, end_time=end_time, precise_time_hour_start_end=precise_time_hour_start_end
    )
    dt_index = pd.DataFrame(
        index=pd.date_range(start=start_time, end=end_time, freq=time_resolution, tz=start_time.tzinfo).tz_convert("UTC")
    )
    df = pd.concat([df, dt_index.loc[~dt_index.index.isin(df.index)]]).sort_index()

    df = df.interpolate(method="time") if interpolate_values else df.ffill()

    df = df[start_time:]

    if default_none_to_zero_if_numeric:
        for column in df.columns:
            if pd.api.types.is_numeric_dtype(df[column].dtype):
                df[column] = df[column].fillna(0)

    return df.loc[df.index.isin(dt_index.index)].copy()


def transform_telemetry_time_weighted_average(
    df: pd.DataFrame, time_resolution: timedelta = timedelta(hours=1), start_time: datetime | None = None, end_time: datetime | None = None
) -> pd.DataFrame:
    """Resample numeric telemetry onto a regular grid using a time-weighted average.

    `df` is the pivoted frame (time in UTC). Each numeric column is weighted by the time
    each reading was in effect and summed per `time_resolution` bucket between
    `start_time` and `end_time` (inferred from the data when omitted). Non-numeric
    columns (arrays, strings and the like) are not averaged; they are forward-filled onto
    the same grid and reattached. Raises a ``ValueError`` if `df` has no numeric column.
    """
    non_transformable_df = []
    transformable_df = df.copy()
    for column in transformable_df.columns:
        if not pd.api.types.is_numeric_dtype(df[column].dtype):
            non_transformable_df.append(transformable_df[column])
            transformable_df = transformable_df.drop(column, axis=1)
    if len(transformable_df.columns) == 0:
        raise ValueError("There must be at least one numerical column to do twa, but there were none.")

    start_time, end_time = _prepare_start_end_time(
        df=transformable_df, start_time=start_time, end_time=end_time, precise_time_hour_start_end=True
    )

    if len(non_transformable_df) > 0:
        non_transformable_df = pd.concat(non_transformable_df, axis=1)
        non_transformable_df = transform_data_forward_fill(
            df=non_transformable_df,
            time_resolution=time_resolution,
            start_time=start_time,
            end_time=end_time,
            precise_time_hour_start_end=True,
            interpolate_values=False,
        )
    else:
        non_transformable_df = pd.DataFrame(index=pd.DatetimeIndex(data=[], tz="UTC"))

    dt_index = pd.DataFrame(
        index=pd.date_range(start=start_time, end=end_time, freq=time_resolution, tz=start_time.tzinfo).tz_convert("UTC")
    )
    transformable_df = pd.concat([transformable_df, dt_index.loc[~dt_index.index.isin(transformable_df.index)]]).sort_index()[start_time:]

    time_difference = transformable_df.index.to_series().diff().shift(-1).dropna() / (
        pd.to_timedelta(time_resolution) / pd.to_timedelta(timedelta(seconds=1))
    )
    transformable_df = transformable_df.mul(time_difference, axis=0).dropna()

    return pd.concat([transformable_df.resample(time_resolution).sum(), non_transformable_df], axis=1)
