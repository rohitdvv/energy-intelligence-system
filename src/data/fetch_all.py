"""Ingestion orchestrator — fetches all production data and saves to data/raw/.

Usage (from project root):
    python src/data/fetch_all.py

API keys must be in a .env file at the project root or in environment variables:
    EIA_API_KEY=...
    FRED_API_KEY=...
    ANTHROPIC_API_KEY=...

Output files (data/raw/):
    eia_oil_<basin>.parquet     — crude oil production proxy (Mbbls/month)
    eia_gas_<basin>.parquet     — natural gas production proxy (MMcf/month)
    wti_prices.parquet          — WTI spot price from FRED ($/bbl, monthly)

A failed individual fetch prints a warning and continues; it does not abort
the full run. Re-run after fixing the underlying issue to fill any gaps.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

# Ensure src/ is importable regardless of invocation directory
_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pandas as pd  # noqa: E402  (after sys.path patch)

from data.eia import BASINS, EIAClient  # noqa: E402
from data.fred import FREDClient  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

_RAW_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _save(df: pd.DataFrame, stem: str) -> None:
    """Write *df* to data/raw/<stem>.parquet, or warn if empty."""
    if df.empty:
        logger.warning("Skipping empty DataFrame — %s", stem)
        return
    path = _RAW_DIR / f"{stem}.parquet"
    df.to_parquet(path, index=False)
    logger.info("  ✓  %s  (%d rows)", path.name, len(df))


def _basin_slug(basin_name: str) -> str:
    return basin_name.lower().replace(" ", "_")


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

def main() -> None:
    _RAW_DIR.mkdir(parents=True, exist_ok=True)

    eia = EIAClient()
    fred = FREDClient()

    print(f"\nFetching {len(BASINS)} basins × 2 fuels from EIA + WTI from FRED")
    print(f"Output directory: {_RAW_DIR}\n")

    success = 0
    failure = 0

    # --- EIA: crude oil production ---
    for basin in BASINS:
        slug = _basin_slug(basin)
        try:
            logger.info("EIA oil  — %s", basin)
            df = eia.fetch_oil_production_by_basin(basin)
            _save(df, f"eia_oil_{slug}")
            success += 1
        except Exception as exc:
            logger.warning("FAILED oil fetch — %s: %s", basin, exc)
            failure += 1

    # --- EIA: natural gas production ---
    for basin in BASINS:
        slug = _basin_slug(basin)
        try:
            logger.info("EIA gas  — %s", basin)
            df = eia.fetch_gas_production_by_basin(basin)
            _save(df, f"eia_gas_{slug}")
            success += 1
        except Exception as exc:
            logger.warning("FAILED gas fetch — %s: %s", basin, exc)
            failure += 1

    # --- FRED: WTI price ---
    try:
        logger.info("FRED WTI — WTISPLC")
        df = fred.fetch_wti_price()
        _save(df, "wti_prices")
        success += 1
    except Exception as exc:
        logger.warning("FAILED WTI fetch from FRED: %s", exc)
        failure += 1

    total = success + failure
    print(f"\nDone — {success}/{total} fetches succeeded", end="")
    print(f", {failure} failed (see warnings above)" if failure else ".")


if __name__ == "__main__":
    main()
