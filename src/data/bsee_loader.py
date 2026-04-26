"""BSEE / BOEM offshore production data fetcher.

Bureau of Safety and Environmental Enforcement (BSEE)
Bureau of Ocean Energy Management (BOEM)

Data source: data.bsee.gov — federal offshore well and production data
Covers: U.S. Outer Continental Shelf (OCS), primarily Gulf of Mexico

What this provides
------------------
- Monthly offshore oil and gas production by area (GOM deepwater, shelf, etc.)
- Federal lease counts and active well inventory
- Complements EIA onshore basin data with offshore federal production context

API
---
BSEE uses a public REST/OData API at data.bsee.gov.
No API key required — public government data.

Fallback
--------
If the live API is unavailable, returns curated static baseline figures
sourced from published BSEE annual production summaries.
"""
from __future__ import annotations

import logging
from functools import lru_cache

import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Static fallback — BSEE annual production summaries
# Source: BSEE Oil and Gas Production Reports (public)
# Units: oil in thousand barrels/year, gas in MMcf/year
# ---------------------------------------------------------------------------
_BSEE_STATIC: dict[int, dict] = {
    2018: {"oil_mbbls": 624_000, "gas_mmcf": 1_180_000, "active_wells": 2_847},
    2019: {"oil_mbbls": 668_000, "gas_mmcf": 1_110_000, "active_wells": 2_754},
    2020: {"oil_mbbls": 642_000, "gas_mmcf": 1_020_000, "active_wells": 2_601},
    2021: {"oil_mbbls": 584_000, "gas_mmcf":   940_000, "active_wells": 2_480},
    2022: {"oil_mbbls": 606_000, "gas_mmcf":   900_000, "active_wells": 2_390},
    2023: {"oil_mbbls": 618_000, "gas_mmcf":   870_000, "active_wells": 2_310},
    2024: {"oil_mbbls": 625_000, "gas_mmcf":   855_000, "active_wells": 2_290},
}

_BSEE_API_BASE = "https://www.data.bsee.gov/api"


@lru_cache(maxsize=8)
def fetch_gom_production(year: int | None = None) -> dict:
    """Fetch Gulf of Mexico offshore production from BSEE API.

    Falls back to static summary data if the API is unavailable.

    Parameters
    ----------
    year : int or None
        Specific year to fetch, or None for the most recent available.

    Returns
    -------
    dict with keys:
        source        : "bsee_api" or "bsee_static"
        year          : int
        oil_mbbls     : annual oil production, thousand barrels
        gas_mmcf      : annual gas production, MMcf
        active_wells  : approximate active well count
        area          : "Gulf of Mexico (Federal OCS)"
    """
    import urllib.request
    import json

    target_year = year or max(_BSEE_STATIC.keys())

    try:
        # BSEE OData production endpoint — annual summary by year
        # OData filter spaces must be URL-encoded
        odata_filter = f"year(productiondate)%20eq%20{target_year}"
        url = (
            f"{_BSEE_API_BASE}/Production/production"
            f"?$filter={odata_filter}"
            f"&$select=productiondate,oilprod,gasprod"
            f"&$top=5000"
            f"&$format=json"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "EnergyIntelligenceSystem/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())

        records = data.get("value", [])
        if records:
            oil_total = sum(float(r.get("oilprod") or 0) for r in records)
            gas_total = sum(float(r.get("gasprod") or 0) for r in records)
            return {
                "source":       "bsee_api",
                "year":         target_year,
                "oil_mbbls":    round(oil_total),
                "gas_mmcf":     round(gas_total),
                "active_wells": _BSEE_STATIC.get(target_year, {}).get("active_wells", "N/A"),
                "area":         "Gulf of Mexico (Federal OCS)",
            }
    except Exception as exc:
        logger.warning("BSEE API unavailable (%s) — using static fallback", exc)

    # Static fallback
    rec = _BSEE_STATIC.get(target_year, _BSEE_STATIC[max(_BSEE_STATIC.keys())])
    return {
        "source":       "bsee_static",
        "year":         target_year,
        "oil_mbbls":    rec["oil_mbbls"],
        "gas_mmcf":     rec["gas_mmcf"],
        "active_wells": rec["active_wells"],
        "area":         "Gulf of Mexico (Federal OCS)",
    }


def fetch_gom_monthly_series(fuel_type: str = "oil") -> pd.DataFrame:
    """Return a monthly production series for GOM (static baseline + trend).

    Used for comparative context in the Overview and Map tabs.

    Parameters
    ----------
    fuel_type : "oil" or "gas"

    Returns
    -------
    DataFrame with columns: ds (datetime), y (production), basin, source
    """
    rows = []
    for yr, rec in sorted(_BSEE_STATIC.items()):
        val = rec["oil_mbbls"] if fuel_type == "oil" else rec["gas_mmcf"]
        monthly = val / 12
        for mo in range(1, 13):
            rows.append({
                "ds":     pd.Timestamp(f"{yr}-{mo:02d}-01"),
                "y":      round(monthly, 1),
                "basin":  "Gulf of Mexico",
                "source": "bsee_static",
            })
    return pd.DataFrame(rows)


def get_offshore_context(target_year: int, fuel_type: str) -> dict:
    """Return a formatted context dict for display in the UI.

    Includes production volume, share of U.S. total context,
    active well count, and regulatory body attribution.
    """
    data = fetch_gom_production(target_year)
    ft   = fuel_type.lower()

    prod  = data["oil_mbbls"]  if ft == "oil" else data["gas_mmcf"]
    unit  = "thousand bbl/yr"  if ft == "oil" else "MMcf/yr"
    share = "15–18% of U.S. total"  if ft == "oil" else "4–6% of U.S. total"

    return {
        "area":         data["area"],
        "year":         data["year"],
        "production":   f"{prod:,.0f} {unit}",
        "us_share":     share,
        "active_wells": data["active_wells"],
        "regulator":    "BSEE / BOEM (U.S. Dept of Interior)",
        "data_source":  data["source"],
        "note": (
            "Federal offshore production is not included in the EIA basin-level "
            "series above. GOM deepwater represents significant additional U.S. supply."
        ),
    }
