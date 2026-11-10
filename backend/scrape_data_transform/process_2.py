
import json
import re
from pathlib import Path
from datetime import datetime

import polars as pl


# ============================================================
# CONFIGURATION
# ============================================================

INPUT_FILE = Path("all_responses.json")

OUTPUT_DIR = Path("output")
PARQUET_FILE = OUTPUT_DIR / "cleaned_market_data.parquet"
CSV_FILE = OUTPUT_DIR / "cleaned_market_data.csv"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# HELPERS
# ============================================================

def extract_metadata(url: str) -> dict:
    """
    Extract instrument token and timeframe from the API URL.

    Example:
    /historical/256265/5minute?user_id=...
    """

    match = re.search(
        r"/historical/(\d+)/(\w+)",
        url
    )

    if match:
        return {
            "instrument_token": match.group(1),
            "timeframe": match.group(2),
        }

    return {
        "instrument_token": None,
        "timeframe": None,
    }


def parse_timestamp(value: str):
    """
    Parse Zerodha timestamps such as:
    2026-10-30T09:15:00+0530

    Returns a timezone-aware datetime.
    """

    if not value:
        return None

    try:
        return datetime.strptime(
            value,
            "%Y-%m-%dT%H:%M:%S%z"
        )

    except ValueError:
        return None


# ============================================================
# LOAD INPUT
# ============================================================

print("Loading input file...")

with INPUT_FILE.open("r", encoding="utf-8") as file:
    responses = json.load(file)

print(f"Total API responses: {len(responses)}")


# ============================================================
# EXTRACT CANDLES
# ============================================================

rows = []

successful_responses = 0
failed_responses = 0
invalid_candles = 0

for response in responses:

    # Ignore failed API requests
    if response.get("status") != 200:
        failed_responses += 1
        continue

    # The body is a JSON string, so parse it
    body_string = response.get("body")

    if not body_string:
        failed_responses += 1
        continue

    try:
        body = json.loads(body_string)

    except (json.JSONDecodeError, TypeError):
        failed_responses += 1
        continue

    # Ignore unsuccessful API responses
    if body.get("status") != "success":
        failed_responses += 1
        continue

    candles = body.get("data", {}).get("candles", [])

    if not candles:
        continue

    successful_responses += 1

    url = response.get("url", "")

    metadata = extract_metadata(url)

    for candle in candles:

        # Expected candle:
        # [timestamp, open, high, low, close, volume, oi]

        if len(candle) < 7:
            invalid_candles += 1
            continue

        timestamp = parse_timestamp(candle[0])

        if timestamp is None:
            invalid_candles += 1
            continue

        try:
            open_price = float(candle[1])
            high_price = float(candle[2])
            low_price = float(candle[3])
            close_price = float(candle[4])

            volume = int(candle[5])
            oi = int(candle[6])

        except (ValueError, TypeError):
            invalid_candles += 1
            continue

        rows.append({
            "timestamp": timestamp,
            "instrument_token": metadata["instrument_token"],
            "timeframe": metadata["timeframe"],
            "open": open_price,
            "high": high_price,
            "low": low_price,
            "close": close_price,
            "volume": volume,
            "oi": oi,
        })


# ============================================================
# CREATE POLARS DATAFRAME
# ============================================================

print("\nCreating Polars DataFrame...")

if not rows:
    raise ValueError(
        "No valid candles found. Check the input file structure."
    )

df = pl.DataFrame(
    rows,
    schema={
        "timestamp": pl.Datetime(
            time_unit="us",
            time_zone="Asia/Kolkata"
        ),
        "instrument_token": pl.String,
        "timeframe": pl.String,
        "open": pl.Float64,
        "high": pl.Float64,
        "low": pl.Float64,
        "close": pl.Float64,
        "volume": pl.Int64,
        "oi": pl.Int64,
    },
    orient="row",
)


# ============================================================
# CLEAN DATA
# ============================================================

print("Cleaning data...")

initial_count = df.height

# Remove duplicate candles using instrument + timeframe + timestamp
df = df.unique(
    subset=[
        "instrument_token",
        "timeframe",
        "timestamp",
    ],
    keep="last",
)

# Sort chronologically
df = df.sort(
    [
        "instrument_token",
        "timeframe",
        "timestamp",
    ]
)

# Remove rows with missing essential values
df = df.drop_nulls(
    subset=[
        "timestamp",
        "open",
        "high",
        "low",
        "close",
    ]
)

# Validate OHLC relationships
df = df.filter(
    (pl.col("high") >= pl.col("low"))
    & (pl.col("high") >= pl.col("open"))
    & (pl.col("high") >= pl.col("close"))
    & (pl.col("low") <= pl.col("open"))
    & (pl.col("low") <= pl.col("close"))
)

final_count = df.height

removed_count = initial_count - final_count


# ============================================================
# SAVE OUTPUT
# ============================================================

print("\nSaving Parquet file...")

df.write_parquet(
    PARQUET_FILE,
    compression="zstd",
)

# Optional CSV export
df.write_csv(CSV_FILE)


# ============================================================
# SUMMARY
# ============================================================

print("\n" + "=" * 50)
print("DATA PROCESSING COMPLETE")
print("=" * 50)

print(f"Total API responses:       {len(responses)}")
print(f"Successful responses:      {successful_responses}")
print(f"Failed responses:          {failed_responses}")
print(f"Invalid candles:           {invalid_candles}")

print(f"\nCandles before cleaning:   {initial_count}")
print(f"Candles removed:           {removed_count}")
print(f"Final candles:             {final_count}")

print(f"\nParquet file: {PARQUET_FILE}")
print(f"CSV file:     {CSV_FILE}")

print("\nData preview:")
print(df.head(10))

print("\nSchema:")
print(df.schema)

print("\nDate range:")

print(
    df.select(
        pl.col("timestamp").min().alias("start"),
        pl.col("timestamp").max().alias("end"),
    )
)
