"""EIA Open Data API v2 client for U.S. oil and gas production data.

Basin-level data is approximated by aggregating state-level production
figures from the EIA crude oil and natural gas production endpoints.
State-to-basin mappings use EIA duoarea codes (convention: "S" + USPS
2-letter state abbreviation, e.g. "STX" = Texas).

Endpoints used:
  Oil  : api.eia.gov/v2/petroleum/crd/crpdn/data  (Mbbls/month)
  Gas  : api.eia.gov/v2/natural-gas/prod/sum/data  (MMcf/month)
  WTI  : api.eia.gov/v2/petroleum/pri/spt/data     ($/bbl)
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
import requests

from config import get_eia_key

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.eia.gov/v2"
_MAX_RETRIES = 3
_BACKOFF_BASE = 2  # seconds

BASINS: list[str] = [
    "Permian",
    "Bakken",
    "Eagle Ford",
    "Marcellus",
    "Haynesville",
    "Anadarko",
    "Appalachian",
]

# Basin → EIA duoarea codes (state proxies).
# Limitation: Texas (STX) covers both Permian and Eagle Ford; when fetching
# either individually the returned total will reflect all TX production.
_BASIN_OIL_AREAS: dict[str, list[str]] = {
    "Permian":      ["STX", "SNM"],       # Texas + New Mexico
    "Bakken":       ["SND"],              # North Dakota
    "Eagle Ford":   ["STX"],              # Texas
    "Marcellus":    ["SPA", "SWV"],       # Pennsylvania + West Virginia
    "Haynesville":  ["SLA"],              # Louisiana
    "Anadarko":     ["SOK"],              # Oklahoma
    "Appalachian":  ["SPA", "SWV", "SOH"],  # PA + WV + Ohio
}

_BASIN_GAS_AREAS: dict[str, list[str]] = {
    "Permian":      ["STX", "SNM"],
    "Bakken":       ["SND"],
    "Eagle Ford":   ["STX"],
    "Marcellus":    ["SPA", "SWV"],
    "Haynesville":  ["SLA"],
    "Anadarko":     ["SOK"],
    "Appalachian":  ["SPA", "SWV", "SOH"],
}


def _start_date() -> str:
    """ISO-8601 month string 15 years before today."""
    return (datetime.now() - timedelta(days=365 * 15)).strftime("%Y-%m")


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=["ds", "y", "basin", "fuel_type"]).astype(
        {"ds": "datetime64[ns]", "y": "float64", "basin": "object", "fuel_type": "object"}
    )


class EIAClient:
    """Thin client for the EIA Open Data API v2 with retry support."""

    def __init__(self) -> None:
        self._api_key = get_eia_key()
        self._session = requests.Session()
        self._session.headers["User-Agent"] = "EnergyIntelligenceSystem/1.0"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get(self, route: str, params: list[tuple[str, str]]) -> dict[str, Any]:
        """Authenticated GET with exponential-backoff retry."""
        url = f"{_BASE_URL}/{route}"
        full_params: list[tuple[str, str]] = [("api_key", self._api_key)] + params

        last_exc: Exception = RuntimeError("no attempts made")
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                resp = self._session.get(url, params=full_params, timeout=30)
                resp.raise_for_status()
                payload: dict[str, Any] = resp.json()
                if "error" in payload:
                    raise ValueError(f"EIA API error: {payload['error']}")
                return payload
            except (requests.RequestException, ValueError) as exc:
                last_exc = exc
                if attempt == _MAX_RETRIES:
                    break
                wait = _BACKOFF_BASE ** attempt
                logger.warning(
                    "EIA request failed (attempt %d/%d): %s — retrying in %ds",
                    attempt, _MAX_RETRIES, exc, wait,
                )
                time.sleep(wait)
        raise last_exc

    def _fetch_production(
        self,
        route: str,
        areas: list[str],
        basin_name: str,
        fuel_type: str,
        process_code: str,
    ) -> pd.DataFrame:
        """Fetch, normalise, and aggregate production rows into a basin DataFrame.

        *process_code* filters to a single EIA process type so the groupby sum
        across states is not inflated by the ~11 process codes the API returns
        per state per month (e.g. gross withdrawal, marketed, vented, flared…).
        """
        params: list[tuple[str, str]] = [
            ("frequency", "monthly"),
            ("data[0]", "value"),
            ("start", _start_date()),
            ("sort[0][column]", "period"),
            ("sort[0][direction]", "asc"),
            ("length", "5000"),
        ]
        for area in areas:
            params.append(("facets[duoarea][]", area))
        params.append(("facets[process][]", process_code))

        payload = self._get(route, params)
        rows: list[dict[str, Any]] = payload.get("response", {}).get("data", [])

        if not rows:
            logger.warning(
                "No rows returned — basin=%s fuel=%s areas=%s",
                basin_name, fuel_type, areas,
            )
            return _empty_frame()

        df = pd.DataFrame(rows)
        df["ds"] = pd.to_datetime(df["period"])
        df["y"] = pd.to_numeric(df["value"], errors="coerce")

        # Sum across multiple states → single basin-level series
        df = df.groupby("ds", as_index=False)["y"].sum()

        # Drop last month if it looks like a partial/incomplete EIA report
        if len(df) >= 7:
            last_val = df["y"].iloc[-1]
            recent_avg = df["y"].iloc[-7:-1].mean()
            if recent_avg > 0 and last_val < 0.5 * recent_avg:
                logger.warning(
                    "Dropping partial month %s: value=%.0f vs recent avg=%.0f",
                    df["ds"].iloc[-1].strftime("%Y-%m"), last_val, recent_avg,
                )
                df = df.iloc[:-1]

        df["basin"] = basin_name
        df["fuel_type"] = fuel_type

        return (
            df[["ds", "y", "basin", "fuel_type"]]
            .sort_values("ds")
            .reset_index(drop=True)
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_oil_production_by_basin(self, basin_name: str) -> pd.DataFrame:
        """Monthly crude oil production proxy for *basin_name*.

        Returns columns: ds (datetime), y (Mbbls/month), basin (str),
        fuel_type='oil'.
        """
        areas = _BASIN_OIL_AREAS.get(basin_name)
        if areas is None:
            raise ValueError(f"Unknown basin {basin_name!r}. Valid: {BASINS}")
        return self._fetch_production(
            "petroleum/crd/crpdn/data", areas, basin_name, "oil",
            process_code="FPF",
        )

    def fetch_gas_production_by_basin(self, basin_name: str) -> pd.DataFrame:
        """Monthly natural gas production proxy for *basin_name*.

        Returns columns: ds (datetime), y (MMcf/month), basin (str),
        fuel_type='gas'.
        """
        areas = _BASIN_GAS_AREAS.get(basin_name)
        if areas is None:
            raise ValueError(f"Unknown basin {basin_name!r}. Valid: {BASINS}")
        return self._fetch_production(
            "natural-gas/prod/sum/data", areas, basin_name, "gas",
            process_code="VGM",
        )

    def fetch_wti_spot_price(self) -> pd.DataFrame:
        """Monthly WTI crude spot price from EIA petroleum spot prices.

        Series: RWTC (WTI Cushing, Oklahoma).
        Returns columns: ds (datetime), y ($/bbl), basin='national',
        fuel_type='wti'.
        """
        params: list[tuple[str, str]] = [
            ("frequency", "monthly"),
            ("data[0]", "value"),
            ("facets[series][]", "RWTC"),
            ("start", _start_date()),
            ("sort[0][column]", "period"),
            ("sort[0][direction]", "asc"),
            ("length", "5000"),
        ]
        payload = self._get("petroleum/pri/spt/data", params)
        rows: list[dict[str, Any]] = payload.get("response", {}).get("data", [])

        if not rows:
            logger.warning("No WTI spot price rows returned from EIA")
            return _empty_frame()

        df = pd.DataFrame(rows)
        df["ds"] = pd.to_datetime(df["period"])
        df["y"] = pd.to_numeric(df["value"], errors="coerce")
        df["basin"] = "national"
        df["fuel_type"] = "wti"

        return (
            df[["ds", "y", "basin", "fuel_type"]]
            .sort_values("ds")
            .reset_index(drop=True)
        )
