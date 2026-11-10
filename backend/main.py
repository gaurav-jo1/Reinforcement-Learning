import logging
from datetime import timezone
from typing import Annotated

import polars as pl
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import AwareDatetime

from indicators import candle_path, mount_indicator_routes

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET"],
    expose_headers=["Content-Disposition"],
)
mount_indicator_routes(app)

logger = logging.getLogger(__name__)
CANDLE_FILE = candle_path()
CANDLE_COLUMNS = (
    "timestamp", "instrument_token", "timeframe",
    "open", "high", "low", "close", "volume", "oi",
)


@app.get("/")
def read_root():
    return {"message": "Hello, FastAPI!"}


@app.get("/candles")
def read_candles(
    start: Annotated[AwareDatetime | None, Query(description="Inclusive start time")] = None,
    end: Annotated[AwareDatetime | None, Query(description="Exclusive end time")] = None,
    limit: Annotated[int, Query(ge=1, le=10_000)] = 2_000,
):
    """Return the most recent matching candles in chronological order."""
    if start is not None and end is not None and start >= end:
        raise HTTPException(status_code=422, detail="start must be earlier than end")

    try:
        candles = pl.scan_parquet(CANDLE_FILE).select(CANDLE_COLUMNS)
        timestamp = pl.col("timestamp")
        timestamp_type = candles.collect_schema()["timestamp"]
        if timestamp_type == pl.String:
            # Kite strings carry an explicit offset, such as +0530.
            timestamp = timestamp.str.to_datetime(
                format="%Y-%m-%dT%H:%M:%S%.f%#z", time_zone="UTC"
            )
        elif isinstance(timestamp_type, pl.Datetime):
            if timestamp_type.time_zone is None:
                timestamp = timestamp.dt.replace_time_zone("Asia/Kolkata")
            if timestamp_type.time_zone != "UTC":
                timestamp = timestamp.dt.convert_time_zone("UTC")
        else:
            raise HTTPException(status_code=503, detail="Unsupported candle timestamp type")

        candles = candles.with_columns(timestamp)
        if start is not None:
            candles = candles.filter(pl.col("timestamp") >= start.astimezone(timezone.utc))
        if end is not None:
            candles = candles.filter(pl.col("timestamp") < end.astimezone(timezone.utc))

        return candles.sort("timestamp").tail(limit).collect().to_dicts()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail="Candle data file is missing") from exc
    except (OSError, pl.exceptions.PolarsError) as exc:
        logger.exception("Unable to read candle Parquet")
        raise HTTPException(status_code=503, detail="Candle data is unavailable") from exc
