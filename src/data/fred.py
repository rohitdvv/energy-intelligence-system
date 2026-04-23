"""FRED (Federal Reserve Bank of St. Louis) API client.

Endpoint: api.stlouisfed.org/fred/series/observations
Docs    : https://fred.stlouisfed.org/docs/api/fred/
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
import requests

from config import get_fred_key

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.stlouisfed.org/fred"
_MAX_RETRIES = 3
_BACKOFF_BASE = 2  # seconds


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=["ds", "y", "basin", "fuel_type"]).astype(
        {"ds": "datetime64[ns]", "y": "float64", "basin": "object", "fuel_type": "object"}
    )


class FREDClient:
    """Thin client for the FRED REST API with retry support."""

    def __init__(self) -> None:
        self._api_key = get_fred_key()
        self._session = requests.Session()
        self._session.headers["User-Agent"] = "EnergyIntelligenceSystem/1.0"

    def _get(self, endpoint: str, params: dict[str, str]) -> dict[str, Any]:
        """Authenticated GET with exponential-backoff retry."""
        url = f"{_BASE_URL}/{endpoint}"
        full_params = {**params, "api_key": self._api_key, "file_type": "json"}

        last_exc: Exception = RuntimeError("no attempts made")
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                resp = self._session.get(url, params=full_params, timeout=30)
                resp.raise_for_status()
                return resp.json()  # type: ignore[no-any-return]
            except requests.RequestException as exc:
                last_exc = exc
                if attempt == _MAX_RETRIES:
                    break
                wait = _BACKOFF_BASE ** attempt
                logger.warning(
                    "FRED request failed (attempt %d/%d): %s — retrying in %ds",
                    attempt, _MAX_RETRIES, exc, wait,
                )
                time.sleep(wait)
        raise last_exc

    def fetch_wti_price(self) -> pd.DataFrame:
        """Monthly average WTI crude oil spot price (FRED series WTISPLC).

        Returns columns: ds (datetime), y ($/bbl), basin='national',
        fuel_type='wti'.

        FRED encodes missing values as the string "."; those rows are dropped.
        """
        start = (datetime.now() - timedelta(days=365 * 15)).strftime("%Y-%m-%d")
        payload = self._get(
            "series/observations",
            {
                "series_id": "WTISPLC",
                "observation_start": start,
                "frequency": "m",
                "aggregation_method": "avg",
                "sort_order": "asc",
            },
        )

        observations: list[dict[str, str]] = payload.get("observations", [])
        if not observations:
            logger.warning("No WTISPLC observations returned from FRED")
            return _empty_frame()

        df = pd.DataFrame(observations)
        df = df[df["value"] != "."].copy()  # drop FRED missing-value sentinels
        df["ds"] = pd.to_datetime(df["date"])
        df["y"] = pd.to_numeric(df["value"], errors="coerce")
        df["basin"] = "national"
        df["fuel_type"] = "wti"

        return (
            df[["ds", "y", "basin", "fuel_type"]]
            .sort_values("ds")
            .reset_index(drop=True)
        )
