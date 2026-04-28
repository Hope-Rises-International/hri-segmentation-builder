"""Cloud Run entry points for the Segmentation Builder.

Entry points (deployed as separate Cloud Functions from the same source):
1. run_segmentation_diagnostic — full legacy pipeline (Apps Script UI, Re-fit)
2. run_sf_extract — nightly SF → GCS → BQ cache (Cloud Scheduler)
3. build_universe_endpoint — Phase 1 of scenario editor: waterfall + suppression
   + baseline rollup → universe JSON returned to browser
4. approve_scenario_endpoint — Phase 3: accept scenario, generate outputs
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
        baseline_appeal_code = config.get("baseline_appeal_code", None) or None
        segment_overrides = config.get("segment_overrides", None) or None
        # Always log config keys for diagnostics
        print(f"Config keys received: {sorted(config.keys())}")
        print(f"Baseline campaign: {baseline_appeal_code or '(not set)'}")
        if toggles:
            print(f"Received toggles from UI: {toggles}")
        if segment_overrides:
            print(f"Segment overrides: {segment_overrides}")
        result = run_diagnostic(
            toggles=toggles,
            baseline_appeal_code=baseline_appeal_code,
            segment_overrides=segment_overrides,
        )
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


@functions_framework.http
def build_universe_endpoint(request):
    """Phase 1 of scenario editor — return universe dataset as JSON to browser.

    Runs waterfall + suppression + baseline rollup. Does NOT do budget
    fitting, output files, or Drive/Sheets writes beyond the Universe tab.
    """
    from src.build_universe import build_universe
    start = time.time()
    try:
        config = {}
        try:
            config = request.get_json(silent=True) or {}
        except Exception:
            pass

        toggles = config.get("toggles", None)
        baseline_appeal_code = config.get("baseline_appeal_code", None) or None
        baseline_type = config.get("baseline_type", None) or None
        campaign_config = {
            "campaign_name": config.get("campaign_name", ""),
            "appeal_code": config.get("appeal_code", ""),
            "budget_qty_mailed": config.get("budget_qty_mailed", 0),
            "budget_cost": config.get("budget_cost", 0),
            "campaign_type": config.get("campaign_type", "Appeal"),
        }
        print(f"build-universe config: {campaign_config}, "
              f"baseline_type={baseline_type}, baseline_code={baseline_appeal_code}")

        result = build_universe(
            toggles=toggles,
            baseline_appeal_code=baseline_appeal_code,
            baseline_type=baseline_type,
            campaign_config=campaign_config,
        )
        duration = time.time() - start
        result["status"] = "success"
        result["duration_seconds"] = round(duration, 1)
        return result, 200
    except Exception as e:
        duration = time.time() - start
        tb = traceback.format_exc()
        print(f"build-universe ERROR after {duration:.0f}s: {e}\n{tb}")
        return {"status": "error", "message": str(e)}, 500


@functions_framework.http
def rebuild_historical_baseline_endpoint(request):
    """Rebuild the sf_cache.historical_baseline table on demand.

    Normally the nightly SF extract also refreshes this — this endpoint is
    for ad-hoc rebuilds after Scorecard data changes.
    """
    from src.sheets_client import get_sheets_client
    from src.historical_baseline import rebuild_and_publish
    start = time.time()
    try:
        summary = rebuild_and_publish(get_sheets_client())
        return {
            "status": "success",
            "duration_seconds": round(time.time() - start, 1),
            **summary,
        }, 200
    except Exception as e:
        tb = traceback.format_exc()
        print(f"rebuild-historical-baseline ERROR: {e}\n{tb}")
        return {"status": "error", "message": str(e)}, 500


@functions_framework.http
def approve_scenario_endpoint(request):
    """Phase 3 of scenario editor — approve a scenario and generate outputs.

    Receives {campaign, baseline_appeal_code, toggles, scenario} from browser.
    Runs full pipeline (waterfall + suppression + baseline + budget fit with
    scenario overrides + ask strings + appeal codes + output files) and
    writes to Drive + MIC. Updates campaign status to Approved.
    """
    from src.approve_scenario import approve_scenario
    start = time.time()
    try:
        config = {}
        try:
            config = request.get_json(silent=True) or {}
        except Exception:
            pass

        campaign_config = config.get("campaign") or {
            "campaign_name": config.get("campaign_name", ""),
            "appeal_code": config.get("appeal_code", ""),
            "budget_qty_mailed": config.get("budget_qty_mailed", 0),
            "budget_cost": config.get("budget_cost", 0),
            "campaign_type": config.get("campaign_type", "Appeal"),
            "lane": config.get("lane", "Housefile"),
        }
        # Operator's selection from the multi-select campaign picker
        # (Item C). Single-campaign callers can omit this; the body
        # of approve_scenario falls back to [campaign_config].
        selected_campaigns = config.get("selected_campaigns") or config.get("campaigns") or []
        # Pass operator email through for the Nuclear audit log
        if "operator" in config and "operator" not in campaign_config:
            campaign_config["operator"] = config.get("operator", "")
        scenario = config.get("scenario") or {}
        toggles = config.get("toggles", None)
        baseline_appeal_code = config.get("baseline_appeal_code", None) or None
        baseline_type = config.get("baseline_type", None) or None
        nuclear = bool(config.get("nuclear", False))

        print(f"approve-scenario: campaign={campaign_config.get('appeal_code')}, "
              f"scenario={scenario.get('name','unnamed')}, "
              f"overrides={len(scenario.get('segments',[]))}, "
              f"baseline_type={baseline_type}, baseline_code={baseline_appeal_code}, "
              f"selected_campaigns={[c.get('appeal_code') for c in selected_campaigns]}, "
              f"nuclear={nuclear}")

        result = approve_scenario(
            campaign_config=campaign_config,
            scenario=scenario,
            toggles=toggles,
            baseline_appeal_code=baseline_appeal_code,
            baseline_type=baseline_type,
            selected_campaigns=selected_campaigns,
            nuclear=nuclear,
        )
        duration = time.time() - start
        result["duration_seconds"] = round(duration, 1)
        return result, 200
    except Exception as e:
        duration = time.time() - start
        tb = traceback.format_exc()
        print(f"approve-scenario ERROR after {duration:.0f}s: {e}\n{tb}")
        return {"status": "error", "message": str(e)}, 500
