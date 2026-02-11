"""
Simple stress harness for RenderDoc MCP file-based bridge.

Usage:
  python scripts/stress_bridge.py --threads 8 --requests 200
  python scripts/stress_bridge.py --method get_bridge_diagnostics --requests 50
"""

import argparse
import json
import os
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from mcp_server.bridge.client import RenderDocBridge, RenderDocBridgeError  # noqa: E402


def parse_args():
    parser = argparse.ArgumentParser(description="Stress-test RenderDoc MCP bridge")
    parser.add_argument("--method", default="get_capture_status", help="Bridge method to call")
    parser.add_argument(
        "--params",
        default="{}",
        help="JSON object string for method params (default: '{}')",
    )
    parser.add_argument("--threads", type=int, default=8, help="Concurrent worker threads")
    parser.add_argument("--requests", type=int, default=200, help="Total request count")
    parser.add_argument(
        "--show-errors",
        type=int,
        default=10,
        help="Number of unique errors to show in summary",
    )
    parser.add_argument(
        "--dump-diagnostics",
        action="store_true",
        help="Print get_bridge_diagnostics() at the end",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        params = json.loads(args.params)
    except json.JSONDecodeError as exc:
        raise SystemExit("Invalid --params JSON: %s" % exc)

    if not isinstance(params, dict):
        raise SystemExit("--params must decode to a JSON object")

    bridge = RenderDocBridge()
    lock = threading.Lock()
    latencies = []
    errors = {}
    successes = 0
    failures = 0

    def run_once(_idx):
        start = time.perf_counter()
        try:
            bridge.call(args.method, params)
            elapsed = time.perf_counter() - start
            return True, elapsed, ""
        except Exception as exc:
            elapsed = time.perf_counter() - start
            return False, elapsed, str(exc)

    started_at = time.time()
    with ThreadPoolExecutor(max_workers=max(1, args.threads)) as executor:
        futures = [executor.submit(run_once, i) for i in range(max(1, args.requests))]
        for future in as_completed(futures):
            ok, elapsed, err = future.result()
            with lock:
                latencies.append(elapsed)
                if ok:
                    successes += 1
                else:
                    failures += 1
                    errors[err] = errors.get(err, 0) + 1

    duration = max(0.0001, time.time() - started_at)
    summary = {
        "method": args.method,
        "params": params,
        "threads": args.threads,
        "requests": args.requests,
        "duration_sec": round(duration, 3),
        "throughput_rps": round(args.requests / duration, 2),
        "successes": successes,
        "failures": failures,
        "failure_rate": round((failures / max(1, args.requests)) * 100.0, 2),
        "latency_ms": {
            "min": round(min(latencies) * 1000.0, 2) if latencies else 0.0,
            "p50": round(statistics.median(latencies) * 1000.0, 2) if latencies else 0.0,
            "p95": round(
                sorted(latencies)[int(len(latencies) * 0.95) - 1] * 1000.0
                if len(latencies) > 1
                else (latencies[0] * 1000.0 if latencies else 0.0),
                2,
            ),
            "max": round(max(latencies) * 1000.0, 2) if latencies else 0.0,
        },
        "top_errors": sorted(errors.items(), key=lambda x: x[1], reverse=True)[: args.show_errors],
    }

    print(json.dumps(summary, indent=2))

    if args.dump_diagnostics:
        try:
            diag = bridge.call(
                "get_bridge_diagnostics",
                {"include_recent_errors": True, "max_recent_errors": 16},
            )
            print("\nBridge diagnostics:")
            print(json.dumps(diag, indent=2))
        except RenderDocBridgeError as exc:
            print("\nBridge diagnostics unavailable: %s" % exc)


if __name__ == "__main__":
    main()
