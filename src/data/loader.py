"""Data loading layer: reads parquet files from data/raw/ with live-fetch fallback.

Usage inside Streamlit (results are cached for 1 h):
    from data.loader import load_production, load_wti

Usage in CLI scripts:
    from data.loader import load_production_no_cache, load_wti_no_cache
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

import pandas as pd

from data.eia import BASINS, EIAClient
from data.fred import FREDClient

logger = logging.getLogger(__name__)

_RAW_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"

_EMPTY = pd.DataFrame(columns=["ds", "y", "basin", "fuel_type"]).astype(
    {"ds": "datetime64[ns]", "y": "float64", "basin": "object", "fuel_type": "object"}
)


# ------------------------------------------------------------------
# Core (no-cache) implementations
# ------------------------------------------------------------------

def _basin_slug(name: str) -> str:
    return name.lower().replace(" ", "_")


def _read_parquet(path: Path) -> pd.DataFrame | None:
    if path.exists() and path.stat().st_size > 0:
        return pd.read_parquet(path)
    return None


def _fetch_and_save(fetcher: Callable[[], pd.DataFrame], path: Path) -> pd.DataFrame:
    """Call *fetcher*, save result to *path*, return DataFrame."""
    df = fetcher()
    if not df.empty:
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path, index=False)
        logger.info("Live-fetched and cached → %s (%d rows)", path.name, len(df))
    return df


def load_production_no_cache(
    fuel_type: str = "oil",
    live_fetch: bool = False,
) -> pd.DataFrame:
    """Load all-basin production for *fuel_type* ('oil' or 'gas').

    If a basin file is missing and *live_fetch* is True, fetches it from
    the EIA API and saves it locally before returning.

    Returns a DataFrame with columns: ds, y, basin, fuel_type.
    """
    frames: list[pd.DataFrame] = []
    eia: EIAClient | None = None

    for basin in BASINS:
        slug = _basin_slug(basin)
        path = _RAW_DIR / f"eia_{fuel_type}_{slug}.parquet"
        df = _read_parquet(path)

        if df is None and live_fetch:
            logger.info("Parquet not found — live-fetching %s %s", fuel_type, basin)
            if eia is None:
                eia = EIAClient()
            fetcher = (
                lambda b=basin: eia.fetch_oil_production_by_basin(b)  # type: ignore[union-attr]
                if fuel_type == "oil"
                else lambda b=basin: eia.fetch_gas_production_by_basin(b)  # type: ignore[union-attr]
            )
            try:
                df = _fetch_and_save(fetcher, path)
            except Exception as exc:
                logger.warning("Live fetch failed — %s %s: %s", fuel_type, basin, exc)

        if df is not None and not df.empty:
            frames.append(df)
        elif df is None:
            logger.debug("Missing parquet for %s %s — skipping", fuel_type, basin)

    if not frames:
        logger.warning(
            "No %s production data found in %s. Run fetch_all.py first.",
            fuel_type, _RAW_DIR,
        )
        return _EMPTY.copy()

    combined = pd.concat(frames, ignore_index=True)
    combined["ds"] = pd.to_datetime(combined["ds"])
    return combined.sort_values(["basin", "ds"]).reset_index(drop=True)


def load_wti_no_cache(live_fetch: bool = False) -> pd.DataFrame:
    """Load WTI monthly price series.

    Falls back to a live FRED fetch if the parquet is missing and
    *live_fetch* is True.
    """
    path = _RAW_DIR / "wti_prices.parquet"
    df = _read_parquet(path)

    if df is None and live_fetch:
        logger.info("wti_prices.parquet not found — live-fetching from FRED")
        try:
            df = _fetch_and_save(FREDClient().fetch_wti_price, path)
        except Exception as exc:
            logger.warning("FRED live fetch failed: %s", exc)

    if df is None or df.empty:
        logger.warning("No WTI price data. Run fetch_all.py or enable live_fetch.")
        return _EMPTY.copy()

    df = df.copy()
    df["ds"] = pd.to_datetime(df["ds"])
    return df.sort_values("ds").reset_index(drop=True)


# ------------------------------------------------------------------
# Streamlit-cached wrappers
# ------------------------------------------------------------------

def _make_cached() -> tuple[Callable, Callable]:
    """Return st.cache_data-wrapped versions if Streamlit is available."""
    try:
        import streamlit as st

        @st.cache_data(ttl=3600, show_spinner="Loading production data…")
        def load_production(fuel_type: str = "oil", live_fetch: bool = False) -> pd.DataFrame:
            """Cached: load all-basin production data."""
            return load_production_no_cache(fuel_type=fuel_type, live_fetch=live_fetch)

        @st.cache_data(ttl=3600, show_spinner="Loading WTI prices…")
        def load_wti(live_fetch: bool = False) -> pd.DataFrame:
            """Cached: load WTI monthly price series."""
            return load_wti_no_cache(live_fetch=live_fetch)

        return load_production, load_wti

    except ImportError:
        return load_production_no_cache, load_wti_no_cache


load_production, load_wti = _make_cached()
