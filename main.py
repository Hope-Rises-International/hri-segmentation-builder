"""Cloud Run entry point for the Segmentation Builder diagnostic."""

import sys
import os
import time
import traceback

import functions_framework

# Add src/ to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from src.run_diagnostic import run_diagnostic


@functions_framework.http
def run_segmentation_diagnostic(request):
    """Phase 1 diagnostic gate for the Segmentation Builder.

    Triggered by Cloud Scheduler or manual invocation.
    """
    start = time.time()
    try:
        result = run_diagnostic()
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
