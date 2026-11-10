"""Build 5-minute indicator Parquet and CSV files and serve the Parquet through FastAPI."""

import argparse
import json
import logging
import os
from datetime import timezone
from itertools import product
from math import isfinite
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated

import polars as pl
from fastapi import APIRouter, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import AwareDatetime

logger = logging.getLogger(__name__)
BAR_MINUTES = 5
CATALOG_KEY = "nifty_indicator_catalog"
KEYS = ["instrument_token", "timeframe", "bar_open_at"]
OUTPUT_KEYS = [*KEYS, "available_at"]

# Edit these candidate settings and rebuild the Parquet file to update the catalog.
GRIDS = {
    "atr": {"length": (7, 14, 21, 28, 50)},
    "ema": {"length": (10, 20, 30, 40, 50, 75, 100, 150, 200)},
    "rsi": {"length": (7, 14, 21, 28)},
    "bollinger": {"length": (10, 20, 30, 50), "multiplier": (1.5, 2.0, 2.5)},
    "donchian": {"length": (10, 20, 30, 50, 75)},
    "dmi": {"length": (7, 14, 21, 28)},
}
MACD_PRESETS = (
    {"fast": 6, "slow": 13, "signal": 5},
    {"fast": 12, "slow": 26, "signal": 9},
    {"fast": 19, "slow": 39, "signal": 9},
    {"fast": 24, "slow": 52, "signal": 18},
)
# Each entry lists the chart series and the suggested model feature outputs.
FAMILIES = {
    "atr": ("Average True Range", ("line",), ("relative",)),
    "ema": ("Exponential Moving Average", ("line",), ("distance",)),
    "rsi": ("Relative Strength Index", ("line",), ("line",)),
    "bollinger": (
        "Bollinger Bands",
        ("middle", "upper", "lower"),
        ("percent_b", "bandwidth_pct"),
    ),
    "donchian": (
        "Donchian Channels",
        ("upper", "lower"),
        ("distance_upper", "distance_lower"),
    ),
    "dmi": ("DMI / ADX", ("adx", "plus_di", "minus_di"), ("adx", "di_spread")),
    "macd": ("MACD", ("line", "signal", "histogram"), ("relative_histogram",)),
}


def master_data_dir() -> Path:
    default = Path(__file__).resolve().parent / "master_data"
    return Path(os.getenv("MASTER_DATA_DIR", str(default)))


def candle_path() -> Path:
    default = master_data_dir() / "nifty_50_5min_20150109-20261103_candle.parquet"
    return Path(os.getenv("CANDLE_FILE", str(default)))


def indicator_path() -> Path:
    default = master_data_dir() / "indicators_v1.parquet"
    return Path(os.getenv("INDICATOR_FILE", str(default)))


def build_catalog() -> dict:
    families = []
    for family, (label, chart, features) in FAMILIES.items():
        grid = GRIDS.get(family)
        presets = (
            [dict(zip(grid, values)) for values in product(*grid.values())]
            if grid is not None
            else MACD_PRESETS
        )
        variants = []
        variant_ids = set()
        for params in presets:
            for name, value in params.items():
                if not isfinite(value) or value <= 0:
                    raise ValueError(f"{family} {name} must be positive and finite")
                if name != "multiplier" and not isinstance(value, int):
                    raise ValueError(f"{family} {name} must be a whole number of candles")
            if family == "macd" and params["fast"] >= params["slow"]:
                raise ValueError("MACD fast period must be shorter than its slow period")
            suffix = "_".join(f"{value:g}".replace(".", "p") for value in params.values())
            variant_id = f"{family}_{suffix}"
            if variant_id in variant_ids:
                raise ValueError(f"Duplicate indicator variant: {variant_id}")
            variant_ids.add(variant_id)
            outputs = {
                name: f"{variant_id}_{name}" for name in dict.fromkeys((*chart, *features))
            }
            if family == "bollinger":
                outputs["middle"] = f"bollinger_{params['length']}_middle"
            variants.append({
                "id": variant_id,
                "params": params,
                "nominal_period_minutes": {
                    name: value * BAR_MINUTES
                    for name, value in params.items() if name != "multiplier"
                },
                "outputs": outputs,
                "chart_outputs": chart,
                "feature_outputs": features,
            })
        if not variants:
            raise ValueError(f"At least one preset is required for {family}")
        families.append({
            "id": family,
            "label": label,
            "parameter_grid": grid,
            "variant_count": len(variants),
            "variants": variants,
        })
    return {
        "schema_version": 1,
        "timeframe_minutes": BAR_MINUTES,
        "family_count": len(families),
        "variant_count": sum(family["variant_count"] for family in families),
        "families": families,
        "calculation": {
            "available_at": "Candle open time plus five minutes",
            "history": "Periods count observed candles and continue across sessions",
            "warmup": "Null until each output has enough observations",
            "ema": "Recursive EMA seeded with first observation; hidden until its period",
            "wilder": "First full-window mean, then Wilder smoothing",
            "bollinger": "Population standard deviation; percent_b is a ratio and bandwidth_pct is percent",
            "donchian": "Channel bounds use the previous N completed candles",
            "dmi": "DI lookback and ADX smoothing use the same length",
        },
    }


def read_candles(path: Path) -> pl.DataFrame:
    candles = pl.scan_parquet(path)
    schema = candles.collect_schema()
    source_time = "bar_open_at" if "bar_open_at" in schema else "timestamp"
    required = ["instrument_token", "timeframe", source_time, "high", "low", "close"]
    missing = sorted(set(required) - set(schema.names()))
    if missing:
        raise ValueError(f"Missing candle columns: {', '.join(missing)}")

    bar_open = pl.col(source_time)
    timestamp_type = schema[source_time]
    if timestamp_type == pl.String:
        bar_open = bar_open.str.to_datetime(
            format="%Y-%m-%dT%H:%M:%S%.f%#z", time_zone="UTC"
        )
    elif isinstance(timestamp_type, pl.Datetime):
        if timestamp_type.time_zone is None:
            bar_open = bar_open.dt.replace_time_zone("Asia/Kolkata")
        bar_open = bar_open.dt.convert_time_zone("UTC")
    else:
        raise ValueError("Candle timestamp must be a datetime or an ISO string with an offset")

    data = candles.select(
        pl.col("instrument_token").cast(pl.Int64),
        pl.col("timeframe").cast(pl.String),
        bar_open.cast(pl.Datetime("us", "UTC")).alias("bar_open_at"),
        pl.col("high", "low", "close").cast(pl.Float64),
    ).sort(KEYS).collect()
    if data.is_empty():
        raise ValueError("The candle file has no rows")
    if data.filter(pl.any_horizontal(pl.col(KEYS).is_null())).height:
        raise ValueError("Candle keys must be present on every row")
    if data.filter(~pl.col("timeframe").is_in(["5minute", "5m"])).height:
        raise ValueError("Use a 5-minute candle file with timeframe '5minute' or '5m'")
    if data.select(KEYS).is_duplicated().any():
        raise ValueError("Duplicate candle keys found. Deduplicate the input before building indicators")
    valid_prices = (
        pl.all_horizontal(pl.col("high", "low", "close").is_finite())
        & (pl.col("low") > 0)
        & (pl.col("low") <= pl.col("close"))
        & (pl.col("close") <= pl.col("high"))
    )
    if data.filter(~valid_prices.fill_null(False)).height:
        raise ValueError("Candles must have finite positive prices and low <= close <= high")
    # Indicators use the current candle's final prices and become available at
    # candle close. A 09:15 five-minute candle's features are available at 09:20.
    # Backtests and training joins must enforce available_at <= decision_time
    # for every decision, including when reading the Parquet or CSV directly.
    return data.with_columns(
        (pl.col("bar_open_at") + pl.duration(minutes=BAR_MINUTES)).alias("available_at")
    )


def wilder(values: pl.Expr, length: int) -> pl.Expr:
    seed = values.rolling_mean(length, min_samples=length)
    return (
        pl.when(seed.is_null()).then(None)
        .when(seed.shift(1).is_null()).then(seed)
        .otherwise(values)
        .ewm_mean(alpha=1 / length, adjust=False, ignore_nulls=True)
    )


def ema(values: pl.Expr, length: int) -> pl.Expr:
    return values.ewm_mean(span=length, adjust=False, min_samples=length, ignore_nulls=True)


def safe_ratio(numerator: pl.Expr, denominator: pl.Expr) -> pl.Expr:
    return numerator / pl.when(denominator != 0).then(denominator)


def indicator_expressions(family: str, params: dict) -> dict[str, pl.Expr]:
    high, low, close = pl.col("high"), pl.col("low"), pl.col("close")
    length = params.get("length")

    if family == "atr":
        atr = wilder(pl.col("_true_range"), length)
        return {"line": atr, "relative": atr / close}

    if family == "ema":
        average = ema(close, length)
        return {"line": average, "distance": (close - average) / average}

    if family == "rsi":
        change = close.diff()
        gains = wilder(change.clip(lower_bound=0), length)
        losses = wilder((-change).clip(lower_bound=0), length)
        return {
            "line": pl.when((gains == 0) & (losses == 0)).then(50.0)
            .otherwise(100 * safe_ratio(gains, gains + losses)),
        }

    if family == "bollinger":
        middle = close.rolling_mean(length, min_samples=length)
        deviation = close.rolling_std(length, min_samples=length, ddof=0) * params["multiplier"]
        upper, lower = middle + deviation, middle - deviation
        return {
            "middle": middle,
            "upper": upper,
            "lower": lower,
            "percent_b": safe_ratio(close - lower, upper - lower),
            "bandwidth_pct": 100 * safe_ratio(upper - lower, middle),
        }

    if family == "donchian":
        upper = high.shift(1).rolling_max(length, min_samples=length)
        lower = low.shift(1).rolling_min(length, min_samples=length)
        return {
            "upper": upper, "lower": lower,
            "distance_upper": (close - upper) / close,
            "distance_lower": (close - lower) / close,
        }

    if family == "dmi":
        up, down = high.diff(), -low.diff()
        plus_dm = pl.when(up.is_null()).then(None).when((up > down) & (up > 0)).then(up).otherwise(0.0)
        minus_dm = pl.when(down.is_null()).then(None).when((down > up) & (down > 0)).then(down).otherwise(0.0)
        atr = wilder(pl.when(up.is_not_null()).then(pl.col("_true_range")), length)
        plus_di = pl.when(atr == 0).then(0.0).otherwise(100 * safe_ratio(wilder(plus_dm, length), atr))
        minus_di = pl.when(atr == 0).then(0.0).otherwise(100 * safe_ratio(wilder(minus_dm, length), atr))
        total = plus_di + minus_di
        dx = pl.when(total == 0).then(0.0).otherwise(100 * safe_ratio((plus_di - minus_di).abs(), total))
        return {
            "adx": wilder(dx, length), "plus_di": plus_di, "minus_di": minus_di,
            "di_spread": plus_di - minus_di,
        }

    if family == "macd":
        line = ema(close, params["fast"]) - ema(close, params["slow"])
        signal = ema(line, params["signal"])
        histogram = line - signal
        return {
            "line": line, "signal": signal, "histogram": histogram,
            "relative_histogram": histogram / close,
        }

    raise ValueError(f"Unsupported indicator family: {family}")


def calculate(candles: pl.DataFrame, catalog: dict) -> pl.DataFrame:
    outputs = {}
    for family in catalog["families"]:
        for variant in family["variants"]:
            expressions = indicator_expressions(family["id"], variant["params"])
            for name, column in variant["outputs"].items():
                outputs.setdefault(column, expressions[name].alias(column))

    groups = candles.partition_by(["instrument_token", "timeframe"], maintain_order=True)
    previous_close = pl.col("close").shift(1)
    true_range = pl.max_horizontal(
        pl.col("high") - pl.col("low"),
        (pl.col("high") - previous_close).abs(),
        (pl.col("low") - previous_close).abs(),
    )
    return pl.concat([
        group.lazy()
        .with_columns(true_range.alias("_true_range"))
        .select(*OUTPUT_KEYS, *outputs.values())
        .collect()
        for group in groups
    ])


def write_indicators(source: Path, output: Path) -> dict:
    csv_output = output.with_suffix(".csv")
    if len({source.resolve(), output.resolve(), csv_output.resolve()}) != 3:
        raise ValueError("Use distinct paths for the candle input, indicator Parquet, and indicator CSV")
    if output.is_dir() or csv_output.is_dir():
        raise ValueError("Indicator output paths must be files")
    catalog = build_catalog()
    indicators = calculate(read_candles(source), catalog)
    catalog["dataset"] = {
        "candle_file": source.name,
        "row_count": indicators.height,
        "indicator_column_count": indicators.width - len(OUTPUT_KEYS),
        "first_bar_open_at": indicators["bar_open_at"].min().isoformat(),
        "last_bar_open_at": indicators["bar_open_at"].max().isoformat(),
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent, prefix=f".{output.name}.") as staging:
        staged_parquet = Path(staging) / output.name
        staged_csv = Path(staging) / csv_output.name
        staged_parquet.touch(mode=0o600)
        staged_csv.touch(mode=0o600)
        indicators.write_parquet(
            staged_parquet,
            compression="zstd",
            row_group_size=16_384,
            metadata={CATALOG_KEY: json.dumps(catalog, separators=(",", ":"))},
        )
        indicators.write_csv(staged_csv, datetime_format="%Y-%m-%dT%H:%M:%S%.6f%:z")
        os.replace(staged_csv, csv_output)
        os.replace(staged_parquet, output)
    return catalog


router = APIRouter(prefix="/indicators", tags=["indicators"])


def read_catalog(path: Path) -> dict:
    try:
        metadata = pl.read_parquet_metadata(path)
        return json.loads(metadata[CATALOG_KEY])
    except FileNotFoundError:
        raise HTTPException(503, "Indicator file missing. Build it first.") from None
    except (KeyError, ValueError, OSError, pl.exceptions.PolarsError):
        logger.exception("Unable to read indicator catalog")
        raise HTTPException(503, "Indicator catalog is unavailable.") from None


@router.get("/catalog")
def get_catalog():
    path = indicator_path()
    if not path.exists():
        try:
            return {**build_catalog(), "dataset": None}
        except ValueError as error:
            raise HTTPException(503, str(error)) from error
    return read_catalog(path)


@router.get("/values")
def get_values(
    variant: Annotated[list[str], Query(min_length=1, max_length=12)],
    start: AwareDatetime | None = None,
    end: AwareDatetime | None = None,
    as_of: AwareDatetime | None = None,
    instrument_token: int | None = None,
    limit: Annotated[int, Query(ge=1, le=10_000)] = 2_000,
):
    """Filter by bar open (start inclusive, end exclusive) and optional availability."""
    if start is not None and end is not None and start >= end:
        raise HTTPException(422, "start must be earlier than end")
    path = indicator_path()
    catalog = read_catalog(path)
    choices = {
        item["id"]: item["outputs"] for family in catalog["families"] for item in family["variants"]
    }
    unknown = sorted(set(variant) - choices.keys())
    if unknown:
        raise HTTPException(422, f"Unknown indicator variants: {', '.join(unknown)}")
    columns = list(dict.fromkeys(column for name in variant for column in choices[name].values()))

    try:
        rows = pl.scan_parquet(path).select(*OUTPUT_KEYS, *columns)
        if start is not None:
            rows = rows.filter(pl.col("bar_open_at") >= start.astimezone(timezone.utc))
        if end is not None:
            rows = rows.filter(pl.col("bar_open_at") < end.astimezone(timezone.utc))
        # Backtest callers must pass as_of=decision_time on each query. Filtering
        # start/end by bar_open_at alone can expose a candle before it closes.
        # Omitting as_of returns historical values without a decision-time guard.
        if as_of is not None:
            rows = rows.filter(pl.col("available_at") <= as_of.astimezone(timezone.utc))
        if instrument_token is not None:
            rows = rows.filter(pl.col("instrument_token") == instrument_token)
        return rows.sort(["bar_open_at", "instrument_token", "timeframe"]).tail(limit).collect().to_dicts()
    except (OSError, pl.exceptions.PolarsError):
        logger.exception("Unable to query indicators")
        raise HTTPException(503, "Indicator values are unavailable.") from None


@router.get(
    "/export.csv",
    response_class=FileResponse,
    responses={
        200: {"content": {"text/csv": {"schema": {"type": "string", "format": "binary"}}}},
        404: {"description": "Indicator CSV has not been built"},
    },
)
def download_csv() -> FileResponse:
    """Download the complete precomputed indicator CSV."""
    path = indicator_path().with_suffix(".csv")
    if not path.is_file():
        raise HTTPException(404, "Indicator CSV is missing. Run indicators.py to build it.")
    return FileResponse(path, media_type="text/csv", filename=path.name)


def mount_indicator_routes(app: FastAPI) -> None:
    if not getattr(app.state, "indicator_routes_mounted", False):
        app.include_router(router)
        app.state.indicator_routes_mounted = True


def create_app() -> FastAPI:
    from main import app

    mount_indicator_routes(app)
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "candles", nargs="?", type=Path, default=candle_path(),
        help="Cleaned 5-minute candle Parquet. Defaults to the backend candle file.",
    )
    parser.add_argument(
        "--output", type=Path, default=indicator_path(),
        help="Indicator Parquet path. CSV is written beside it with the same name stem.",
    )
    args = parser.parse_args()
    try:
        result = write_indicators(args.candles, args.output)
    except (ValueError, OSError, pl.exceptions.PolarsError) as error:
        parser.exit(1, f"Indicator build failed: {error}\n")
    print(
        f"Wrote {result['dataset']['row_count']:,} rows for "
        f"{result['variant_count']} variants to {args.output} and {args.output.with_suffix('.csv')}"
    )
