"""Diagnostic output builder: distribution tables, field checks, gate evaluation."""

from __future__ import annotations
import logging

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# RFM Distribution
# ---------------------------------------------------------------------------

def build_rfm_crosstab_rf(rfm_df: pd.DataFrame) -> pd.DataFrame:
    """Cross-tab: Recency × Frequency (account counts)."""
    ct = pd.crosstab(rfm_df["R_bucket"], rfm_df["F_bucket"], margins=True)
    ct.index.name = "Recency \\ Frequency"
    return ct.reset_index()


def build_rfm_crosstab_rm(rfm_df: pd.DataFrame) -> pd.DataFrame:
    """Cross-tab: Recency × Monetary (account counts)."""
    ct = pd.crosstab(rfm_df["R_bucket"], rfm_df["M_bucket"], margins=True)
    ct.index.name = "Recency \\ Monetary"
    return ct.reset_index()


def build_rfm_summary(rfm_df: pd.DataFrame) -> pd.DataFrame:
    """Per-bucket summary: count, %, mean gift, median gift."""
    rows = []
    total = len(rfm_df)

    for axis, col in [("Recency", "R_bucket"), ("Frequency", "F_bucket"), ("Monetary", "M_bucket")]:
        for bucket in sorted(rfm_df[col].unique()):
            subset = rfm_df[rfm_df[col] == bucket]
            count = len(subset)
            avg_5yr = subset["avg_gift_5yr"].dropna()
            rows.append({
                "Axis": axis,
                "Bucket": bucket,
                "Count": count,
                "Pct_of_Total": round(count / total * 100, 1) if total > 0 else 0,
                "Mean_Gift_5yr": round(avg_5yr.mean(), 2) if len(avg_5yr) > 0 else None,
                "Median_Gift_5yr": round(avg_5yr.median(), 2) if len(avg_5yr) > 0 else None,
            })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# HPC / MRC Diagnostic
# ---------------------------------------------------------------------------

def build_hpc_mrc_diagnostic(accounts_df: pd.DataFrame) -> pd.DataFrame:
    """HPC and MRC field population and distribution check."""
    rows = []
    total = len(accounts_df)

    for field, label in [
        ("npo02__LargestAmount__c", "HPC (Largest Gift)"),
        ("npo02__LastOppAmount__c", "MRC (Last Gift Amount)"),
    ]:
        series = pd.to_numeric(accounts_df[field], errors="coerce")
        null_count = series.isna().sum()
        zero_count = (series == 0).sum()
        valid = series.dropna()
        valid_nonzero = valid[valid > 0]

        rows.append({
            "Field": label,
            "Total_Accounts": total,
            "Null_Count": int(null_count),
            "Null_Pct": round(null_count / total * 100, 2) if total > 0 else 0,
            "Zero_Count": int(zero_count),
            "Min": round(valid_nonzero.min(), 2) if len(valid_nonzero) > 0 else None,
            "P25": round(valid_nonzero.quantile(0.25), 2) if len(valid_nonzero) > 0 else None,
            "Median": round(valid_nonzero.median(), 2) if len(valid_nonzero) > 0 else None,
            "P75": round(valid_nonzero.quantile(0.75), 2) if len(valid_nonzero) > 0 else None,
            "Max": round(valid_nonzero.max(), 2) if len(valid_nonzero) > 0 else None,
            "Mean": round(valid_nonzero.mean(), 2) if len(valid_nonzero) > 0 else None,
        })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Sustainer Diagnostic
# ---------------------------------------------------------------------------

def build_sustainer_diagnostic(
    accounts_df: pd.DataFrame, sustainer_field_exists: bool
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Miracle_Partner__c distribution + 20-account spot-check.

    Returns (summary_df, spot_check_df).
    """
    # Summary
    mp = accounts_df["Miracle_Partner__c"]
    summary_rows = [
        {"Field": "Miracle_Partner__c", "Value": "True", "Count": int((mp == True).sum())},
        {"Field": "Miracle_Partner__c", "Value": "False", "Count": int((mp == False).sum())},
        {"Field": "Miracle_Partner__c", "Value": "Null/Missing", "Count": int(mp.isna().sum())},
    ]

    if sustainer_field_exists:
        summary_rows.append({
            "Field": "npsp__Sustainer__c",
            "Value": "(field exists — check values in SF)",
            "Count": None,
        })
    else:
        summary_rows.append({
            "Field": "npsp__Sustainer__c",
            "Value": "(field does NOT exist on Account)",
            "Count": 0,
        })

    summary_df = pd.DataFrame(summary_rows)

    # Spot-check: 20 accounts where Miracle_Partner__c = True
    sustainers = accounts_df[accounts_df["Miracle_Partner__c"] == True]
    spot = sustainers.head(20)[
        ["Id", "Name", "npo02__LastCloseDate__c", "npo02__NumberOfClosedOpps__c",
         "npo02__TotalOppAmount__c"]
    ].copy()
    spot.columns = ["Account_Id", "Name", "Last_Gift_Date", "Lifetime_Gifts", "Lifetime_Total"]

    return summary_df, spot


# ---------------------------------------------------------------------------
# Staff Manager Diagnostic
# ---------------------------------------------------------------------------

def build_staff_manager_diagnostic(accounts_df: pd.DataFrame) -> pd.DataFrame:
    """Staff_Manager__c population and distribution."""
    sm = accounts_df["Staff_Manager__c"]
    populated = sm.notna() & (sm != "")
    pop_count = int(populated.sum())
    null_count = int((~populated).sum())

    rows = [
        {"Metric": "Accounts with Staff_Manager__c", "Value": pop_count},
        {"Metric": "Accounts without Staff_Manager__c", "Value": null_count},
        {"Metric": "Unique Gift Officers", "Value": int(sm[populated].nunique())},
    ]

    # Top 10 officers by account count
    if pop_count > 0:
        top = sm[populated].value_counts().head(10)
        for i, (officer, count) in enumerate(top.items(), 1):
            rows.append({"Metric": f"Officer #{i}: {officer}", "Value": int(count)})

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Cornerstone Diagnostic
# ---------------------------------------------------------------------------

def build_cornerstone_diagnostic(
    accounts_df: pd.DataFrame, rfm_df: pd.DataFrame
) -> pd.DataFrame:
    """Cornerstone_Partner__c distribution + RFM cross-reference."""
    cp = accounts_df.set_index("Id")["Cornerstone_Partner__c"]
    true_count = int((cp == True).sum())
    false_count = int((cp == False).sum())
    null_count = int(cp.isna().sum())

    rows = [
        {"Metric": "Cornerstone = True", "Value": true_count},
        {"Metric": "Cornerstone = False", "Value": false_count},
        {"Metric": "Cornerstone = Null", "Value": null_count},
    ]

    # RFM distribution of Cornerstone donors
    if true_count > 0:
        cs_ids = cp[cp == True].index
        cs_rfm = rfm_df.loc[rfm_df.index.isin(cs_ids)]
        for bucket_col, label in [("R_bucket", "Recency"), ("M_bucket", "Monetary")]:
            dist = cs_rfm[bucket_col].value_counts().sort_index()
            for bucket, count in dist.items():
                rows.append({"Metric": f"CS {label}: {bucket}", "Value": int(count)})

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Gate Criteria Evaluation
# ---------------------------------------------------------------------------

def evaluate_gate_criteria(
    accounts_df: pd.DataFrame,
    rfm_df: pd.DataFrame,
    sustainer_field_exists: bool,
) -> pd.DataFrame:
    """Evaluate Phase 1 diagnostic gate criteria.

    Returns DataFrame with pass/fail per gate.
    """
    total = len(accounts_df)
    results = []

    # Gate (a): HPC and MRC populated (null rate < 5%)
    hpc_nulls = pd.to_numeric(accounts_df["npo02__LargestAmount__c"], errors="coerce").isna().sum()
    mrc_nulls = pd.to_numeric(accounts_df["npo02__LastOppAmount__c"], errors="coerce").isna().sum()
    hpc_pct = round(hpc_nulls / total * 100, 2) if total > 0 else 100
    mrc_pct = round(mrc_nulls / total * 100, 2) if total > 0 else 100
    gate_a = hpc_pct < 5 and mrc_pct < 5
    results.append({
        "Gate": "A: HPC/MRC Populated",
        "Status": "PASS" if gate_a else "FAIL",
        "Detail": f"HPC null: {hpc_pct}% ({int(hpc_nulls):,}/{total:,}), "
                  f"MRC null: {mrc_pct}% ({int(mrc_nulls):,}/{total:,}). "
                  f"Threshold: <5%",
    })

    # Gate (b): Miracle_Partner__c identifies sustainers (>100 True)
    mp_true = int((accounts_df["Miracle_Partner__c"] == True).sum())
    gate_b = mp_true > 100
    results.append({
        "Gate": "B: Sustainer Identification",
        "Status": "PASS" if gate_b else "FAIL",
        "Detail": f"Miracle_Partner__c = True: {mp_true:,} accounts. "
                  f"npsp__Sustainer__c exists: {sustainer_field_exists}. "
                  f"Threshold: >100",
    })

    # Gate (c): Staff_Manager__c identifies portfolio donors (>50 populated)
    sm = accounts_df["Staff_Manager__c"]
    sm_populated = int((sm.notna() & (sm != "")).sum())
    gate_c = sm_populated > 50
    results.append({
        "Gate": "C: Major Gift Portfolio",
        "Status": "PASS" if gate_c else "FAIL",
        "Detail": f"Staff_Manager__c populated: {sm_populated:,} accounts. "
                  f"Threshold: >50",
    })

    # Supplemental: RFM distribution check (no zero-count buckets, no >60%)
    rfm_check_issues = []
    for col in ["R_bucket", "F_bucket", "M_bucket"]:
        dist = rfm_df[col].value_counts()
        for bucket_label in ["R1", "R2", "R3", "R4", "R5", "F1", "F2", "F3", "F4",
                             "M1", "M2", "M3", "M4", "M5"]:
            if bucket_label.startswith(col[0]) and bucket_label not in dist.index:
                rfm_check_issues.append(f"{bucket_label} has 0 accounts")
        max_pct = dist.max() / len(rfm_df) * 100 if len(rfm_df) > 0 else 0
        if max_pct > 60:
            top_bucket = dist.idxmax()
            rfm_check_issues.append(f"{top_bucket} has {max_pct:.1f}% of donors (>60%)")

    rfm_ok = len(rfm_check_issues) == 0
    results.append({
        "Gate": "RFM Distribution Check",
        "Status": "PASS" if rfm_ok else "REVIEW",
        "Detail": "; ".join(rfm_check_issues) if rfm_check_issues else "All buckets populated, no single bucket >60%",
    })

    return pd.DataFrame(results)
