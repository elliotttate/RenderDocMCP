"""
RenderDoc Bridge Client
Communicates with the RenderDoc extension via file-based IPC.
"""

import json
import os
import tempfile
import time
import uuid
from typing import Any


# IPC directory (must match renderdoc_extension/socket_server.py)
IPC_DIR = os.path.join(tempfile.gettempdir(), "renderdoc_mcp")
REQUEST_FILE = os.path.join(IPC_DIR, "request.json")  # legacy single-slot request path
REQUEST_FILE_PREFIX = "request."
INFLIGHT_REQUEST_FILE = os.path.join(IPC_DIR, "request.inflight.json")  # legacy
INFLIGHT_REQUEST_FILE_PREFIX = "request.inflight."
HEARTBEAT_FILE = os.path.join(IPC_DIR, "heartbeat")
RESPONSE_FILE = os.path.join(IPC_DIR, "response.json")  # legacy shared response path
RESPONSE_FILE_PREFIX = "response."

# Heartbeat is stale if older than this many seconds
HEARTBEAT_MAX_AGE = float(os.environ.get("RENDERDOC_MCP_HEARTBEAT_MAX_AGE", "30.0"))
HEARTBEAT_STARTUP_GRACE = float(
    os.environ.get("RENDERDOC_MCP_HEARTBEAT_STARTUP_GRACE", "8.0")
)
HEARTBEAT_MISSING_FAIL_FAST = float(
    os.environ.get("RENDERDOC_MCP_HEARTBEAT_MISSING_FAIL_FAST", "10.0")
)
# Treat very old heartbeat as dead extension and fail fast before enqueue.
HEARTBEAT_STALE_FAIL_FAST_AGE = float(
    os.environ.get("RENDERDOC_MCP_HEARTBEAT_STALE_FAIL_FAST_AGE", "120.0")
)
# Long RenderDoc API calls can stall heartbeat updates on some hosts.
# Keep in-request fail-fast opt-in so we don't spuriously drop valid requests.
HEARTBEAT_FAIL_FAST_DURING_REQUEST = os.environ.get(
    "RENDERDOC_MCP_HEARTBEAT_FAIL_FAST_DURING_REQUEST", "0"
).lower() in ("1", "true", "yes", "on")

# Timeouts (seconds)
RAW_DEFAULT_TIMEOUT = float(os.environ.get("RENDERDOC_MCP_TIMEOUT", "180.0"))
RAW_METHOD_TIMEOUTS = {
    # Keep open_capture timeout conservative so a stalled load doesn't block
    # client workflows for minutes on unstable RenderDoc forks.
    "open_capture": float(os.environ.get("RENDERDOC_MCP_TIMEOUT_OPEN_CAPTURE", "45.0")),
    "get_draw_calls": float(os.environ.get("RENDERDOC_MCP_TIMEOUT_GET_DRAW_CALLS", "240.0")),
    "get_pipeline_state": float(os.environ.get("RENDERDOC_MCP_TIMEOUT_GET_PIPELINE_STATE", "240.0")),
    "get_texture_data": float(os.environ.get("RENDERDOC_MCP_TIMEOUT_GET_TEXTURE_DATA", "240.0")),
    "get_buffer_contents": float(os.environ.get("RENDERDOC_MCP_TIMEOUT_GET_BUFFER_CONTENTS", "240.0")),
}
# 0 disables the cap. Non-zero values clamp all method timeouts.
# Default is disabled so long operations like open_capture do not get truncated.
HARD_TIMEOUT_CAP = float(os.environ.get("RENDERDOC_MCP_HARD_TIMEOUT_CAP", "0.0"))


def _env_truthy(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def _cap_timeout(timeout_seconds: float) -> float:
    timeout_seconds = max(1.0, float(timeout_seconds))
    if HARD_TIMEOUT_CAP > 0.0:
        timeout_seconds = min(timeout_seconds, HARD_TIMEOUT_CAP)
    return timeout_seconds


DEFAULT_TIMEOUT = _cap_timeout(RAW_DEFAULT_TIMEOUT)
METHOD_TIMEOUTS = {name: _cap_timeout(value) for name, value in RAW_METHOD_TIMEOUTS.items()}

REQUEST_ENQUEUE_TIMEOUT = float(os.environ.get("RENDERDOC_MCP_REQUEST_ENQUEUE_TIMEOUT", "30.0"))
# Grace window for per-request spool adoption before falling back to legacy
# request.json mode for older extension installs.
REQUEST_CLAIM_GRACE = max(
    0.05, float(os.environ.get("RENDERDOC_MCP_REQUEST_CLAIM_GRACE", "0.8"))
)
DISABLE_LEGACY_FALLBACK = _env_truthy("RENDERDOC_MCP_DISABLE_LEGACY_FALLBACK", "0")


class RenderDocBridgeError(Exception):
    """Error communicating with RenderDoc bridge"""

    pass


def _get_heartbeat_age() -> float | None:
    """Return age of heartbeat file in seconds, or None if missing/unreadable."""
    try:
        with open(HEARTBEAT_FILE, "r") as f:
            ts = float(f.read().strip())
        return time.time() - ts
    except Exception:
        return None


def _safe_remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _wait_for_heartbeat(max_wait: float) -> float | None:
    last_age = _get_heartbeat_age()
    # If heartbeat exists (fresh or stale), return immediately. Startup grace is
    # only for the "missing heartbeat file" race right after RenderDoc launch.
    if last_age is not None:
        return last_age

    deadline = time.time() + max(0.0, max_wait)
    while True:
        if time.time() >= deadline:
            return None
        time.sleep(0.05)
        last_age = _get_heartbeat_age()
        if last_age is not None:
            return last_age


def _response_file_path_for(request_id: str) -> str:
    return os.path.join(IPC_DIR, "%s%s.json" % (RESPONSE_FILE_PREFIX, request_id))


def _request_file_path_for(request_id: str) -> str:
    return os.path.join(IPC_DIR, "%s%s.json" % (REQUEST_FILE_PREFIX, request_id))


def _write_json_atomic(path: str, payload: dict[str, Any]) -> None:
    tmp_path = "%s.tmp.%d.%s" % (path, os.getpid(), uuid.uuid4().hex)
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    os.replace(tmp_path, path)


def _enqueue_request(request: dict[str, Any], request_id: str, enqueue_timeout: float) -> str:
    """
    Enqueue request using per-request spool files.

    This avoids single-slot request clobbering under concurrent clients/processes.
    """
    request_file_path = _request_file_path_for(request_id)
    _safe_remove(request_file_path)
    deadline = time.time() + max(1.0, enqueue_timeout)

    while True:
        try:
            _write_json_atomic(request_file_path, request)
            return request_file_path
        except OSError:
            # Transient sharing/permission errors.
            pass

        if time.time() >= deadline:
            inflight = os.path.exists(INFLIGHT_REQUEST_FILE)
            legacy_queued = os.path.exists(REQUEST_FILE)
            try:
                pending = len(
                    [
                        name
                        for name in os.listdir(IPC_DIR)
                        if name.startswith(REQUEST_FILE_PREFIX)
                        and name.endswith(".json")
                        and not name.startswith(INFLIGHT_REQUEST_FILE_PREFIX)
                    ]
                )
            except Exception:
                pending = -1
            raise RenderDocBridgeError(
                "Timed out waiting to enqueue request "
                "(pending=%s legacy_queued=%s inflight=%s)." % (pending, legacy_queued, inflight)
            )
        time.sleep(0.02)


def _has_any_inflight_requests() -> bool:
    if os.path.exists(INFLIGHT_REQUEST_FILE):
        return True
    try:
        for name in os.listdir(IPC_DIR):
            lower = name.lower()
            if lower.startswith(INFLIGHT_REQUEST_FILE_PREFIX) and lower.endswith(".json"):
                return True
    except Exception:
        pass
    return False


def _enqueue_legacy_request(request: dict[str, Any], enqueue_timeout: float) -> str:
    """
    Fallback for older extension builds that only consume request.json.
    """
    deadline = time.time() + max(1.0, enqueue_timeout)
    while True:
        if not os.path.exists(REQUEST_FILE) and not os.path.exists(INFLIGHT_REQUEST_FILE):
            try:
                _write_json_atomic(REQUEST_FILE, request)
                return REQUEST_FILE
            except OSError:
                pass

        if time.time() >= deadline:
            raise RenderDocBridgeError(
                "Timed out waiting to enqueue legacy request (request.json busy)."
            )
        time.sleep(0.02)


def _fallback_to_legacy_request_if_needed(
    request: dict[str, Any],
    request_file_path: str,
    enqueue_timeout: float,
) -> tuple[str, bool]:
    """
    Auto-detect extension compatibility and fallback to request.json when
    per-request spool files are not being consumed.
    """
    if DISABLE_LEGACY_FALLBACK:
        return request_file_path, False

    # Already in legacy mode.
    if os.path.basename(request_file_path).lower() == "request.json":
        return request_file_path, True

    # Give modern extension a brief chance to claim request.<id>.json.
    deadline = time.time() + REQUEST_CLAIM_GRACE
    while time.time() < deadline:
        if not os.path.exists(request_file_path):
            return request_file_path, False
        if _has_any_inflight_requests():
            # Bridge is actively working; don't force mode switch while busy.
            return request_file_path, False
        time.sleep(0.02)

    # Likely running an older extension build that only watches request.json.
    # Fallback is safe even when the extension is unhealthy; if nothing is
    # servicing requests, normal request timeout diagnostics still apply.
    _safe_remove(request_file_path)
    legacy_path = _enqueue_legacy_request(request, enqueue_timeout)
    return legacy_path, True


class RenderDocBridge:
    """Client for communicating with RenderDoc extension via file-based IPC"""

    def __init__(self, host: str = "127.0.0.1", port: int = 19876):
        # host/port are kept for API compatibility but not used
        self.host = host
        self.port = port
        self.timeout = DEFAULT_TIMEOUT

    def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Call a method on the RenderDoc extension."""
        # Check if IPC directory exists
        if not os.path.exists(IPC_DIR):
            raise RenderDocBridgeError(
                "RenderDoc is not running (no IPC directory). "
                "Start RenderDoc with the MCP Bridge extension loaded."
            )

        # Check heartbeat freshness for fast failure detection
        hb_age = _wait_for_heartbeat(HEARTBEAT_STARTUP_GRACE)
        if hb_age is None:
            raise RenderDocBridgeError(
                "RenderDoc extension is not running (no heartbeat file). "
                "Start RenderDoc with the MCP Bridge extension loaded."
            )
        preflight_stale = hb_age > HEARTBEAT_MAX_AGE
        if preflight_stale and hb_age >= max(HEARTBEAT_MAX_AGE, HEARTBEAT_STALE_FAIL_FAST_AGE):
            raise RenderDocBridgeError(
                "RenderDoc heartbeat is stale (age=%.1fs). "
                "Extension is likely not running. Restart RenderDoc (or clear stale files in %s)."
                % (hb_age, IPC_DIR)
            )

        request_timeout = METHOD_TIMEOUTS.get(method, self.timeout)
        request_id = str(uuid.uuid4())
        response_file_path = _response_file_path_for(request_id)
        request = {
            "id": request_id,
            "method": method,
            "params": params or {},
            "timeout": request_timeout,
            # Per-request response file prevents cross-client response races.
            "response_file": os.path.basename(response_file_path),
        }

        request_file_path = None
        legacy_mode = False
        try:
            # Clean up stale response file for this exact request id.
            _safe_remove(response_file_path)

            enqueue_timeout = max(REQUEST_ENQUEUE_TIMEOUT, request_timeout)
            request_file_path = _enqueue_request(request, request_id, enqueue_timeout)
            request_file_path, legacy_mode = _fallback_to_legacy_request_if_needed(
                request,
                request_file_path,
                enqueue_timeout,
            )

            # Wait for response
            start_time = time.time()
            stale_count = 0
            unhealthy_heartbeat_since: float | None = None
            response_paths = [response_file_path]
            if RESPONSE_FILE != response_file_path:
                response_paths.append(RESPONSE_FILE)
            while True:
                response = None
                matched_response_path = None
                for candidate_path in response_paths:
                    if not os.path.exists(candidate_path):
                        continue

                    # Read response with retry loop for partial writes
                    for attempt in range(3):
                        try:
                            with open(candidate_path, "r", encoding="utf-8") as f:
                                raw = f.read()
                            response = json.loads(raw)
                            matched_response_path = candidate_path
                            break
                        except (json.JSONDecodeError, OSError):
                            if attempt < 2:
                                time.sleep(0.05 * (attempt + 1))
                            else:
                                raise RenderDocBridgeError(
                                    "Failed to parse response JSON after 3 attempts"
                                )
                    if response is not None:
                        break

                if response is not None:
                    # Clean up the response file we consumed.
                    if matched_response_path:
                        _safe_remove(matched_response_path)

                    # Verify response ID matches request
                    resp_id = response.get("id")
                    if resp_id and resp_id != request_id:
                        stale_count += 1
                        if stale_count >= 3:
                            raise RenderDocBridgeError(
                                "Got %d stale responses (expected id %s). "
                                "Extension may be in a bad state." % (stale_count, request_id)
                            )
                        continue

                    if "error" in response:
                        error = response["error"]
                        if isinstance(error, dict):
                            raise RenderDocBridgeError(
                                f"[{error.get('code', '?')}] {error.get('message', str(error))}"
                            )
                        raise RenderDocBridgeError(str(error))

                    result = response.get("result")
                    if isinstance(result, dict) and "error" in result:
                        raise RenderDocBridgeError(str(result["error"]))

                    return result

                current_hb_age = _get_heartbeat_age()
                if HEARTBEAT_FAIL_FAST_DURING_REQUEST and HEARTBEAT_MISSING_FAIL_FAST > 0.0:
                    # Missing heartbeat is a stronger dead-process signal than "stale" heartbeat.
                    # Stale heartbeat alone can happen during long C++ calls.
                    heartbeat_missing = current_hb_age is None
                    if heartbeat_missing:
                        if unhealthy_heartbeat_since is None:
                            unhealthy_heartbeat_since = time.time()
                        elif (time.time() - unhealthy_heartbeat_since) >= HEARTBEAT_MISSING_FAIL_FAST:
                            raise RenderDocBridgeError(
                                "RenderDoc heartbeat became missing while waiting for '%s' "
                                "(missing for %.1fs)."
                                % (method, HEARTBEAT_MISSING_FAIL_FAST)
                            )
                    else:
                        unhealthy_heartbeat_since = None

                # Check timeout with diagnostic info
                if time.time() - start_time > request_timeout:
                    hb_age = _get_heartbeat_age()
                    request_mode = "legacy" if legacy_mode else "spool"
                    diag = "method=%s, waited=%.1fs, mode=%s" % (
                        method,
                        request_timeout,
                        request_mode,
                    )
                    if hb_age is not None:
                        diag += ", heartbeat_age=%.1fs" % hb_age
                        if hb_age > HEARTBEAT_MAX_AGE:
                            diag += " (STALE - extension likely dead)"
                        else:
                            diag += " (alive - handler may be stuck)"
                    else:
                        diag += ", heartbeat=missing"
                    if preflight_stale:
                        diag += ", preflight=stale-heartbeat"
                    raise RenderDocBridgeError(
                        "Request timed out (%s)" % diag
                    )

                # Poll interval
                time.sleep(0.05)

        except RenderDocBridgeError:
            raise
        except Exception as e:
            raise RenderDocBridgeError(f"Communication error: {e}")
        finally:
            # Best-effort cleanup for abandoned per-request files.
            _safe_remove(response_file_path)
            _safe_remove(RESPONSE_FILE)
            if request_file_path:
                _safe_remove(request_file_path)
