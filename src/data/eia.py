"""EIA Open Data API v2 client for U.S. oil and gas production data.

Basin figures come from EIA's own regional production estimates, published
with the Short-Term Energy Outlook (STEO). EIA builds each region from the
counties over the play, so the regions do not overlap: Permian is West Texas
and southeast New Mexico, Eagle Ford is South Texas, and Appalachia covers
the Marcellus and Utica. STEO series are daily rates (million b/d for oil,
billion cubic feet per day of marketed gas) and are converted to monthly
volumes here. STEO data also contains EIA's forecast, so months after the
latest month of EIA's reported state data are dropped.

Anadarko has no STEO region, so it is still the Oklahoma state total.

Endpoints used:
  Regions : api.eia.gov/v2/steo/data                 (COPR<code>, NGMP<code>)
  Oil     : api.eia.gov/v2/petroleum/crd/crpdn/data  (Mbbls/month, state level)
  Gas     : api.eia.gov/v2/natural-gas/prod/sum/data (MMcf/month, state level)
  WTI     : api.eia.gov/v2/petroleum/pri/spt/data    ($/bbl)
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
import requests

from config import get_eia_key
from observability import redact, span

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.eia.gov/v2"
_MAX_RETRIES = 3
_BACKOFF_BASE = 2  # seconds

BASINS: list[str] = [
    "Permian",
    "Bakken",
    "Eagle Ford",
    "Haynesville",
    "Appalachia",
    "Anadarko",
]

# Basin → STEO region code. Series are COPR<code> (crude oil, million b/d)
# and NGMP<code> (marketed natural gas, Bcf/d).
STEO_REGIONS: dict[str, str] = {
    "Permian":     "PM",
    "Bakken":      "BK",
    "Eagle Ford":  "EF",
    "Haynesville": "HA",
    "Appalachia":  "AP",
}

# Basins without a STEO region fall back to whole-state EIA data.
_STATE_PROXY_AREAS: dict[str, list[str]] = {
    "Anadarko": ["SOK"],
}

# What each basin's figures measure, shown next to every number.
BASIN_SOURCE: dict[str, str] = {
    "Permian":     "EIA region",
    "Bakken":      "EIA region",
    "Eagle Ford":  "EIA region",
    "Haynesville": "EIA region",
    "Appalachia":  "EIA region",
    "Anadarko":    "OK state data",
}

_SCOPE: dict[str, str] = {
    "Permian":     "EIA's Permian region (West Texas and southeast New Mexico counties)",
    "Bakken":      "EIA's Bakken region (North Dakota and Montana counties)",
    "Eagle Ford":  "EIA's Eagle Ford region (South Texas counties)",
    "Haynesville": "EIA's Haynesville region (Louisiana and East Texas counties)",
    "Appalachia":  "EIA's Appalachia region (Marcellus and Utica counties in PA, WV and OH)",
    "Anadarko":    "all of Oklahoma (state data; EIA publishes no Anadarko region)",
}


def basin_scope(basin: str) -> str:
    """Plain description of what a basin's figures cover."""
    return _SCOPE.get(basin, "unknown scope")


def is_state_proxy(basin: str) -> bool:
    return basin in _STATE_PROXY_AREAS


def _start_date() -> str:
    """ISO-8601 month string 15 years before today."""
    return (datetime.now() - timedelta(days=365 * 15)).strftime("%Y-%m")


def steo_rows_to_monthly(rows: list[dict[str, Any]], last_reported: pd.Timestamp) -> pd.DataFrame:
    """Convert STEO daily-rate rows to monthly volumes, dropping forecast months.

    Oil: million b/d x 1,000 x days in month = Mbbls/month.
    Gas: Bcf/d x 1,000 x days in month = MMcf/month.
    """
    df = pd.DataFrame(rows)
    df["ds"] = pd.to_datetime(df["period"])
    rate = pd.to_numeric(df["value"], errors="coerce")
    df["y"] = rate * 1_000 * df["ds"].dt.days_in_month
    df = df[df["ds"] <= last_reported].dropna(subset=["y"])
    return df.sort_values("ds").reset_index(drop=True)


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
        self._reported: dict[str, pd.Timestamp] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get(self, route: str, params: list[tuple[str, str]]) -> dict[str, Any]:
        """Authenticated GET with exponential-backoff retry."""
        url = f"{_BASE_URL}/{route}"
        full_params: list[tuple[str, str]] = [("api_key", self._api_key)] + params

        with span("api", "eia._get", source="eia", route=route) as sp:
            last_exc: Exception = RuntimeError("no attempts made")
            for attempt in range(1, _MAX_RETRIES + 1):
                try:
                    resp = self._session.get(url, params=full_params, timeout=30)
                    sp["status_code"] = resp.status_code
                    resp.raise_for_status()
                    payload: dict[str, Any] = resp.json()
                    if "error" in payload:
                        raise ValueError(f"EIA API error: {payload['error']}")
                    sp["retries"] = attempt - 1
                    return payload
                except (requests.RequestException, ValueError) as exc:
                    # The original message contains the URL with api_key, so a
                    # new exception replaces it and the chain is never kept.
                    safe_message = redact(exc)
                    last_exc = requests.RequestException(safe_message)
                    if attempt == _MAX_RETRIES:
                        break
                    wait = _BACKOFF_BASE ** attempt
                    logger.warning(
                        "EIA request failed (attempt %d/%d): %s — retrying in %ds",
                        attempt, _MAX_RETRIES, safe_message, wait,
                    )
                    time.sleep(wait)
            sp["retries"] = _MAX_RETRIES
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

    def _last_reported_month(self, fuel_type: str) -> pd.Timestamp:
        """Latest month of EIA's reported U.S. production; later STEO months are forecast."""
        if fuel_type not in self._reported:
            route, process = (
                ("petroleum/crd/crpdn/data", "FPF") if fuel_type == "oil"
                else ("natural-gas/prod/sum/data", "VGM")
            )
            try:
                payload = self._get(route, [
                    ("frequency", "monthly"),
                    ("data[0]", "value"),
                    ("facets[duoarea][]", "NUS"),
                    ("facets[process][]", process),
                    ("sort[0][column]", "period"),
                    ("sort[0][direction]", "desc"),
                    ("length", "1"),
                ])
                rows = payload.get("response", {}).get("data", [])
                self._reported[fuel_type] = pd.Timestamp(rows[0]["period"])
            except Exception as exc:  # noqa: BLE001 — fall back to a safe lag
                logger.warning("Could not read EIA's latest reported month: %s", redact(exc))
                today = pd.Timestamp.now().normalize().replace(day=1)
                self._reported[fuel_type] = today - pd.DateOffset(months=3)
        return self._reported[fuel_type]

    def _fetch_steo_region(self, basin_name: str, fuel_type: str) -> pd.DataFrame:
        """Monthly volumes for one STEO region, history only."""
        code = STEO_REGIONS[basin_name]
        series = f"COPR{code}" if fuel_type == "oil" else f"NGMP{code}"
        payload = self._get("steo/data", [
            ("frequency", "monthly"),
            ("data[0]", "value"),
            ("facets[seriesId][]", series),
            ("start", _start_date()),
            ("sort[0][column]", "period"),
            ("sort[0][direction]", "asc"),
            ("length", "5000"),
        ])
        rows: list[dict[str, Any]] = payload.get("response", {}).get("data", [])
        if not rows:
            logger.warning("No STEO rows for %s (%s)", basin_name, series)
            return _empty_frame()
        df = steo_rows_to_monthly(rows, self._last_reported_month(fuel_type))
        df["basin"] = basin_name
        df["fuel_type"] = fuel_type
        return df[["ds", "y", "basin", "fuel_type"]]

    def fetch_oil_production_by_basin(self, basin_name: str) -> pd.DataFrame:
        """Monthly crude oil production for *basin_name*, in Mbbls/month."""
        if basin_name in STEO_REGIONS:
            return self._fetch_steo_region(basin_name, "oil")
        areas = _STATE_PROXY_AREAS.get(basin_name)
        if areas is None:
            raise ValueError(f"Unknown basin {basin_name!r}. Valid: {BASINS}")
        return self._fetch_production(
            "petroleum/crd/crpdn/data", areas, basin_name, "oil", process_code="FPF",
        )

    def fetch_gas_production_by_basin(self, basin_name: str) -> pd.DataFrame:
        """Monthly marketed natural gas production for *basin_name*, in MMcf/month."""
        if basin_name in STEO_REGIONS:
            return self._fetch_steo_region(basin_name, "gas")
        areas = _STATE_PROXY_AREAS.get(basin_name)
        if areas is None:
            raise ValueError(f"Unknown basin {basin_name!r}. Valid: {BASINS}")
        return self._fetch_production(
            "natural-gas/prod/sum/data", areas, basin_name, "gas", process_code="VGM",
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
