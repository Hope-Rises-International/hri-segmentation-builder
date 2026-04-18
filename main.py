"""Cloud Run entry points for the Segmentation Builder.

Two entry points (deployed as separate Cloud Functions from the same source):
1. run_segmentation_diagnostic — full pipeline (triggered by Apps Script UI)
2. run_sf_extract — nightly SF → GCS → BQ cache (triggered by Cloud Scheduler)
"""

import sys
import os
import time
import traceback

import functions_framework

# Add src/ to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))


@functions_framework.http
def run_segmentation_diagnostic(request):
    """Full segmentation pipeline. Triggered by Apps Script UI."""
    from src.run_diagnostic import run_diagnostic
    start = time.time()
    try:
        # Parse request payload for toggle overrides
        config = {}
        try:
            config = request.get_json(silent=True) or {}
        except Exception:
            pass
        toggles = config.get("toggles", None)
        if toggles:
            print(f"Received toggles from UI: {toggles}")
        result = run_diagnostic(toggles=toggles)
        duration = time.time() - start
        return {
            "status": "success",
            "duration_seconds": round(duration, 1),
            "gate_results": result["gate_results"],
            "diagnostic_sheet_url": result["sheet_url"],
            "counts": result["counts"],
        }, 200
    except Exception as e:
        duration = time.time() - start
        tb = traceback.format_exc()
        print(f"ERROR after {duration:.0f}s: {e}\n{tb}")
        return {"status": "error", "message": str(e)}, 500


@functions_framework.http
def run_sf_extract(request):
    """Nightly SF extract: Salesforce → GCS → BigQuery."""
    from src.bq_extract import run_nightly_extract
    start = time.time()
    try:
        result = run_nightly_extract()
        duration = time.time() - start
        return {
            "status": "success",
            "duration_seconds": round(duration, 1),
            "accounts_raw": result.get("accounts_raw", 0),
            "accounts_final": result.get("accounts_final", 0),
            "opportunities_raw": result.get("opportunities_raw", 0),
            "is_cbnc_count": result.get("is_cbnc_count", 0),
            "has_dm_gift_500_count": result.get("has_dm_gift_500_count", 0),
            "timings": result.get("timings", {}),
        }, 200
    except Exception as e:
        duration = time.time() - start
        tb = traceback.format_exc()
        print(f"ERROR after {duration:.0f}s: {e}\n{tb}")
        return {"status": "error", "message": str(e)}, 500
