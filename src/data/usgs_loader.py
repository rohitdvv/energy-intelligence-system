"""USGS National Oil and Gas Assessment — resource adequacy data.

Source
------
U.S. Geological Survey, Energy Resources Program.
All figures represent *mean* undiscovered technically recoverable resources
(UTRR) from the most recent published USGS assessment for each basin.

Assessment reports referenced
------------------------------
Permian (2018)  : USGS Fact Sheet 2018-3101
Bakken  (2013)  : USGS Fact Sheet 2013-3013
Niobrara(2016)  : USGS Fact Sheet 2016-3032
Marcellus(2019) : USGS Open-File Report 2019-1075
Haynesville(2019): USGS Scientific Investigations Report 2019-5094
Anadarko(2015)  : USGS Digital Data Series DDS-069-EE
Eagle Ford(2020): USGS Open-File Report 2020-1104

Units
-----
oil_bbo  : undiscovered oil, mean estimate, billion barrels (BBO)
gas_tcfg : undiscovered natural gas, mean estimate, trillion cubic feet (TCFG)
ngl_bbo  : undiscovered natural-gas liquids, mean estimate, BBO
assessment_year: year of the most recent published USGS assessment
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Static USGS UTRR reference table
# (units: oil/ngl in BBO, gas in TCFG)
# ---------------------------------------------------------------------------
USGS_ASSESSMENTS: dict[str, dict] = {
    "Permian": {
        "oil_bbo":         46.3,
        "gas_tcfg":       281.0,
        "ngl_bbo":         20.0,
        "assessment_year": 2018,
        "report":          "USGS FS 2018-3101",
        "notes":           "Wolfcamp and Bone Spring formations; largest USGS assessment ever",
    },
    "Bakken": {
        "oil_bbo":          7.4,
        "gas_tcfg":          6.7,
        "ngl_bbo":           0.5,
        "assessment_year":  2013,
        "report":           "USGS FS 2013-3013",
        "notes":            "Bakken and Three Forks formations, Williston Basin",
    },
    "Eagle Ford": {
        "oil_bbo":          8.5,
        "gas_tcfg":         66.0,
        "ngl_bbo":           4.3,
        "assessment_year":  2020,
        "report":           "USGS OFR 2020-1104",
        "notes":            "Eagle Ford Group, onshore Texas",
    },
    "Marcellus": {
        "oil_bbo":          0.0,
        "gas_tcfg":         97.0,
        "ngl_bbo":           4.0,
        "assessment_year":  2019,
        "report":           "USGS OFR 2019-1075",
        "notes":            "Marcellus and Utica shales, Appalachian Basin",
    },
    "Haynesville": {
        "oil_bbo":          0.0,
        "gas_tcfg":        196.0,
        "ngl_bbo":           0.5,
        "assessment_year":  2019,
        "report":           "USGS SIR 2019-5094",
        "notes":            "Haynesville-Bossier shale, East Texas/NW Louisiana",
    },
    "Anadarko": {
        "oil_bbo":          0.5,
        "gas_tcfg":          5.2,
        "ngl_bbo":           0.4,
        "assessment_year":  2015,
        "report":           "USGS DDS-069-EE",
        "notes":            "Woodford Shale and Mississippian limestone, Anadarko Basin",
    },
    "Niobrara": {
        "oil_bbo":          1.08,
        "gas_tcfg":          1.68,
        "ngl_bbo":           0.13,
        "assessment_year":  2016,
        "report":           "USGS FS 2016-3032",
        "notes":            "Niobrara Formation, DJ Basin, Colorado/Wyoming",
    },
}


def get_resource_assessment(basin: str) -> dict | None:
    """Return USGS UTRR assessment for *basin*, or None if not found."""
    return USGS_ASSESSMENTS.get(basin)


def resource_adequacy_years(
    basin: str,
    fuel_type: str,
    current_annual_production: float,
) -> float | None:
    """Estimate years of undiscovered resource relative to current production.

    Parameters
    ----------
    basin:
        Basin name matching USGS_ASSESSMENTS keys.
    fuel_type:
        ``"oil"`` or ``"gas"``.
    current_annual_production:
        Annual production in thousand barrels (oil) or MMcf (gas).

    Returns
    -------
    float
        UTRR / annual_production in years, or None if data unavailable.
    """
    rec = get_resource_assessment(basin)
    if rec is None or current_annual_production <= 0:
        return None

    ft = fuel_type.lower()
    if ft == "oil":
        utrr_mbbls = rec["oil_bbo"] * 1_000_000  # BBO → thousand barrels
        return round(utrr_mbbls / current_annual_production, 1)
    elif ft == "gas":
        utrr_mmcf = rec["gas_tcfg"] * 1_000_000   # TCFG → MMcf
        return round(utrr_mmcf / current_annual_production, 1)
    return None


def all_assessments_df():
    """Return all assessments as a pandas DataFrame for display."""
    import pandas as pd
    rows = []
    for basin, rec in USGS_ASSESSMENTS.items():
        rows.append({
            "Basin":           basin,
            "Oil UTRR (BBO)":  rec["oil_bbo"],
            "Gas UTRR (TCFG)": rec["gas_tcfg"],
            "NGL UTRR (BBO)":  rec["ngl_bbo"],
            "Assessed":        rec["assessment_year"],
            "Report":          rec["report"],
        })
    return pd.DataFrame(rows)
