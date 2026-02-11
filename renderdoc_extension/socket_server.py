"""
File-based IPC Server for RenderDoc MCP Bridge
Uses file polling since RenderDoc's Python doesn't have socket/QtNetwork modules.
"""

import json
import os
import traceback
import tempfile
import threading
import time


# IPC directory
IPC_DIR = os.path.join(tempfile.gettempdir(), "renderdoc_mcp")
REQUEST_FILE = os.path.join(IPC_DIR, "request.json")
REQUEST_FILE_PREFIX = "request."
RESPONSE_FILE = os.path.join(IPC_DIR, "response.json")
LOCK_FILE = os.path.join(IPC_DIR, "lock")
HEARTBEAT_FILE = os.path.join(IPC_DIR, "heartbeat")
DIAGNOSTICS_FILE = os.path.join(IPC_DIR, "bridge_diagnostics.json")
LOG_FILE = os.path.join(IPC_DIR, "bridge_server.log")
INFLIGHT_REQUEST_FILE = os.path.join(IPC_DIR, "request.inflight.json")
INFLIGHT_REQUEST_FILE_PREFIX = "request.inflight."
RESPONSE_FILE_PREFIX = "response."

# Processing timeout: if a handler is stuck for longer than this, force-reset
PROCESSING_TIMEOUT = float(os.environ.get("RENDERDOC_MCP_PROCESSING_TIMEOUT", "420.0"))
HEARTBEAT_INTERVAL = float(os.environ.get("RENDERDOC_MCP_HEARTBEAT_INTERVAL", "1.0"))


class MCPBridgeServer:
    """File-based IPC server for MCP bridge communication"""

    def __init__(self, host, port, handler):
        self.handler = handler
        self._thread = None
        self._running = False
        self._processing_since = None  # timestamp when processing started, or None
        self._current_request_id = None  # id of the request being processed
        self._current_request_method = None
        self._current_response_path = RESPONSE_FILE
        self._current_processing_timeout = PROCESSING_TIMEOUT
        self._heartbeat_thread = None
        self._started_at = time.time()
        self._stats_lock = threading.Lock()
        self._metrics = {
            "total_received": 0,
            "total_completed": 0,
            "total_errors": 0,
            "total_timeouts": 0,
            "total_stale_responses": 0,
            "total_json_errors": 0,
            "total_poll_errors": 0,
        }
        self._last_request = None
        self._recent_errors = []
        self._terminal_request_ids = set()
        self._terminal_request_order = []

        # Create IPC directory
        if not os.path.exists(IPC_DIR):
            os.makedirs(IPC_DIR)

    def _log(self, msg):
        try:
            if not os.path.exists(IPC_DIR):
                os.makedirs(IPC_DIR)
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
        except Exception:
            pass

    def start(self):
        """Start the server with polling"""
        self._running = True

        # Clean up old files
        self._cleanup_files()

        # Start polling thread (check every 100ms)
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._heartbeat_thread.start()

        print("[MCP Bridge] File-based IPC server started")
        print("[MCP Bridge] IPC directory: %s" % IPC_DIR)
        self._log("server started")
        return True

    def stop(self):
        """Stop the server"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._heartbeat_thread:
            self._heartbeat_thread.join(timeout=2.0)
            self._heartbeat_thread = None
        self._cleanup_files()
        # Remove heartbeat file
        try:
            if os.path.exists(HEARTBEAT_FILE):
                os.remove(HEARTBEAT_FILE)
        except Exception:
            pass
        self._log("server stopped")
        print("[MCP Bridge] Server stopped")

    def is_running(self):
        """Check if server is running"""
        return self._running

    def _cleanup_files(self):
        """Remove IPC files"""
        for f in [REQUEST_FILE, INFLIGHT_REQUEST_FILE, LOCK_FILE]:
            try:
                if os.path.exists(f):
                    os.remove(f)
            except Exception:
                pass
        for f in [DIAGNOSTICS_FILE]:
            try:
                if os.path.exists(f):
                    os.remove(f)
                if os.path.exists(f + ".tmp"):
                    os.remove(f + ".tmp")
            except Exception:
                pass
        # Remove shared and per-request response files from previous runs.
        try:
            for name in os.listdir(IPC_DIR):
                lower = name.lower()
                if (
                    lower == "response.json"
                    or lower == "response.json.tmp"
                    or (lower.startswith(REQUEST_FILE_PREFIX) and lower.endswith(".json"))
                    or (lower.startswith(REQUEST_FILE_PREFIX) and lower.endswith(".json.tmp"))
                    or (lower.startswith(INFLIGHT_REQUEST_FILE_PREFIX) and lower.endswith(".json"))
                    or (lower.startswith(INFLIGHT_REQUEST_FILE_PREFIX) and lower.endswith(".json.tmp"))
                    or (lower.startswith(RESPONSE_FILE_PREFIX) and lower.endswith(".json"))
                    or (lower.startswith(RESPONSE_FILE_PREFIX) and lower.endswith(".json.tmp"))
                    or lower == "bridge_diagnostics.json"
                    or lower == "bridge_diagnostics.json.tmp"
                ):
                    path = os.path.join(IPC_DIR, name)
                    try:
                        os.remove(path)
                    except Exception:
                        pass
        except Exception:
            pass

    def _write_response(self, response, response_path=None):
        """Write response atomically using temp file + rename"""
        if not response_path:
            response_path = RESPONSE_FILE
        self._write_json_atomic(response_path, response)

    @staticmethod
    def _write_json_atomic(path, payload):
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp_path, path)

    def _resolve_response_path(self, request):
        """
        Resolve the response path requested by the client.
        Falls back to shared response.json for backwards compatibility.
        """
        if not isinstance(request, dict):
            return RESPONSE_FILE
        candidate = request.get("response_file")
        if not isinstance(candidate, str) or not candidate.strip():
            return RESPONSE_FILE

        candidate = candidate.strip()
        if os.path.isabs(candidate):
            path = os.path.normpath(candidate)
        else:
            path = os.path.normpath(os.path.join(IPC_DIR, candidate))

        ipc_abs = os.path.abspath(IPC_DIR)
        path_abs = os.path.abspath(path)
        try:
            in_ipc_dir = os.path.commonpath([ipc_abs, path_abs]) == ipc_abs
        except ValueError:
            in_ipc_dir = False
        if not in_ipc_dir:
            self._log("invalid response_file outside IPC_DIR: %s" % candidate)
            return RESPONSE_FILE

        return path_abs

    def _write_heartbeat(self):
        """Write current timestamp to heartbeat file"""
        try:
            with open(HEARTBEAT_FILE, "w") as f:
                f.write("%.3f" % time.time())
        except Exception:
            pass

    def _poll_loop(self):
        """Background thread that polls for requests"""
        while self._running:
            try:
                self._poll_request()
            except Exception as e:
                # Avoid flooding stdout/stderr; excessive prints can block and
                # stall the bridge thread when no console is attached.
                self._log("poll loop error: %s\n%s" % (str(e), traceback.format_exc()))
            time.sleep(0.1)

    def _heartbeat_loop(self):
        """Dedicated heartbeat thread so heartbeat survives long/slow requests."""
        while self._running:
            try:
                self._write_heartbeat()
                self._write_diagnostics_snapshot()
            except Exception:
                pass
            time.sleep(HEARTBEAT_INTERVAL)

    def _resolve_processing_timeout(self, request):
        """Derive processing timeout for the current request."""
        timeout = PROCESSING_TIMEOUT
        if isinstance(request, dict):
            hinted = request.get("timeout")
            if hinted is not None:
                try:
                    hinted = float(hinted)
                    if hinted > 0.0:
                        # Clamp to sane bounds to avoid accidental infinite waits.
                        timeout = max(30.0, min(hinted, 1800.0))
                except Exception:
                    pass
        return timeout

    def _list_pending_request_files(self):
        """
        Return pending request file paths ordered oldest-first.

        Supports both:
        - legacy single-slot request.json
        - per-request spool files request.<id>.json
        """
        pending = []
        try:
            for name in os.listdir(IPC_DIR):
                lower = name.lower()
                if lower == "request.json":
                    pending.append(os.path.join(IPC_DIR, name))
                    continue
                if (
                    lower.startswith(REQUEST_FILE_PREFIX)
                    and lower.endswith(".json")
                    and not lower.startswith(INFLIGHT_REQUEST_FILE_PREFIX)
                ):
                    pending.append(os.path.join(IPC_DIR, name))
        except Exception:
            return []

        def _sort_key(path):
            try:
                return os.path.getmtime(path)
            except Exception:
                return time.time()

        pending.sort(key=_sort_key)
        return pending

    def _inflight_path_for_request_file(self, request_path):
        """Compute inflight file path for a claimed request file."""
        basename = os.path.basename(request_path)
        lower = basename.lower()
        if lower == "request.json":
            return INFLIGHT_REQUEST_FILE

        suffix = basename[len("request."):]
        return os.path.join(IPC_DIR, "%s%s" % (INFLIGHT_REQUEST_FILE_PREFIX, suffix))

    def _claim_next_request_file(self):
        """Atomically claim the next pending request file for processing."""
        for path in self._list_pending_request_files():
            basename = os.path.basename(path).lower()

            # Legacy compatibility: request.json may still be protected by lock.
            if basename == "request.json" and os.path.exists(LOCK_FILE):
                continue

            inflight_path = self._inflight_path_for_request_file(path)
            try:
                os.replace(path, inflight_path)
                return inflight_path
            except FileNotFoundError:
                continue
            except OSError:
                continue

        return None

    def _trim_recent_errors_locked(self, max_items=32):
        if len(self._recent_errors) > max_items:
            self._recent_errors = self._recent_errors[-max_items:]

    def _record_recent_error(self, kind, request_id=None, method=None, message=""):
        now = time.time()
        with self._stats_lock:
            self._recent_errors.append(
                {
                    "timestamp": now,
                    "kind": kind,
                    "request_id": request_id,
                    "method": method,
                    "message": message,
                }
            )
            self._trim_recent_errors_locked()

    def _record_request_start(self, request_id, method):
        with self._stats_lock:
            self._metrics["total_received"] += 1

    def _mark_terminal_request_locked(self, request_id):
        if not request_id:
            return True
        if request_id in self._terminal_request_ids:
            return False
        self._terminal_request_ids.add(request_id)
        self._terminal_request_order.append(request_id)
        if len(self._terminal_request_order) > 2048:
            stale = self._terminal_request_order.pop(0)
            self._terminal_request_ids.discard(stale)
        return True

    def _record_request_finish(
        self, request_id, method, status, duration_sec=0.0, error_message=None
    ):
        now = time.time()
        with self._stats_lock:
            if not self._mark_terminal_request_locked(request_id):
                return
            self._metrics["total_completed"] += 1
            if status in ("error", "exception", "timeout"):
                self._metrics["total_errors"] += 1
            if status == "timeout":
                self._metrics["total_timeouts"] += 1
            if status == "stale_discarded":
                self._metrics["total_stale_responses"] += 1
            if status == "json_error":
                self._metrics["total_json_errors"] += 1
            self._last_request = {
                "timestamp": now,
                "request_id": request_id,
                "method": method,
                "status": status,
                "duration_sec": round(float(duration_sec), 4),
                "error": error_message or "",
            }

        if error_message:
            self._record_recent_error(
                "request_%s" % status,
                request_id=request_id,
                method=method,
                message=error_message,
            )

    def _get_queue_snapshot(self):
        pending = self._list_pending_request_files()
        now = time.time()
        oldest_age = None
        for path in pending:
            try:
                age = now - os.path.getmtime(path)
                if oldest_age is None or age > oldest_age:
                    oldest_age = age
            except Exception:
                pass

        inflight_count = 0
        try:
            for name in os.listdir(IPC_DIR):
                lower = name.lower()
                if lower.startswith(INFLIGHT_REQUEST_FILE_PREFIX) and lower.endswith(".json"):
                    inflight_count += 1
            if os.path.exists(INFLIGHT_REQUEST_FILE):
                inflight_count += 1
        except Exception:
            pass

        return {
            "pending_count": len(pending),
            "legacy_request_present": os.path.exists(REQUEST_FILE),
            "inflight_count": inflight_count,
            "oldest_pending_age_sec": round(oldest_age, 3) if oldest_age is not None else None,
        }

    def get_diagnostics(self, include_recent_errors=True, max_recent_errors=16):
        now = time.time()
        processing_elapsed = None
        if self._processing_since is not None:
            processing_elapsed = max(0.0, now - self._processing_since)

        with self._stats_lock:
            metrics = dict(self._metrics)
            last_request = dict(self._last_request) if self._last_request else None
            recent_errors = list(self._recent_errors)

        if include_recent_errors:
            recent_errors = recent_errors[-max(1, int(max_recent_errors)) :]
        else:
            recent_errors = []

        heartbeat_age = None
        try:
            with open(HEARTBEAT_FILE, "r") as f:
                ts = float(f.read().strip())
            heartbeat_age = now - ts
        except Exception:
            heartbeat_age = None

        return {
            "schema_version": "bridge_diagnostics.v1",
            "running": self._running,
            "uptime_sec": round(max(0.0, now - self._started_at), 3),
            "heartbeat_age_sec": round(heartbeat_age, 3) if heartbeat_age is not None else None,
            "queue": self._get_queue_snapshot(),
            "processing": {
                "active": self._processing_since is not None,
                "request_id": self._current_request_id,
                "method": self._current_request_method,
                "response_file": os.path.basename(self._current_response_path)
                if self._current_response_path
                else "",
                "elapsed_sec": round(processing_elapsed, 3)
                if processing_elapsed is not None
                else None,
                "timeout_sec": float(self._current_processing_timeout),
            },
            "metrics": metrics,
            "last_request": last_request,
            "recent_errors": recent_errors,
        }

    def _write_diagnostics_snapshot(self):
        try:
            payload = self.get_diagnostics(include_recent_errors=False, max_recent_errors=0)
            self._write_json_atomic(DIAGNOSTICS_FILE, payload)
        except Exception:
            pass

    def _poll_request(self):
        """Check for incoming request"""
        if not self._running:
            return

        # Check if currently processing and whether it's timed out
        if self._processing_since is not None:
            elapsed = time.time() - self._processing_since
            if elapsed < self._current_processing_timeout:
                return  # Still processing, within timeout
            # Processing has been stuck too long - force reset and write error response
            self._log("WARNING handler stuck for %.1fs, force-resetting" % elapsed)
            timed_out_request_id = self._current_request_id
            timed_out_method = self._current_request_method
            timed_out_response_path = self._current_response_path
            timeout_used = self._current_processing_timeout
            self._processing_since = None
            self._current_request_id = None
            self._current_request_method = None
            self._current_response_path = RESPONSE_FILE
            self._current_processing_timeout = PROCESSING_TIMEOUT
            # Write an error response so the client doesn't wait the full timeout
            try:
                error_response = {
                    "id": timed_out_request_id,
                    "error": {"code": -32603, "message": "Handler timed out after %.0fs" % timeout_used}
                }
                self._write_response(error_response, timed_out_response_path)
            except Exception:
                pass
            self._record_request_finish(
                timed_out_request_id,
                timed_out_method,
                "timeout",
                duration_sec=elapsed,
                error_message="Handler timed out after %.0fs" % timeout_used,
            )

        inflight_request_path = self._claim_next_request_file()
        if not inflight_request_path:
            return

        try:
            # Read request
            with open(inflight_request_path, "r", encoding="utf-8") as f:
                request = json.load(f)

            # Remove claimed request file
            try:
                os.remove(inflight_request_path)
            except OSError:
                pass

            # Process request in a worker thread so the poll loop stays alive
            self._current_request_id = request.get("id")
            self._current_request_method = request.get("method")
            self._current_response_path = self._resolve_response_path(request)
            self._processing_since = time.time()
            self._current_processing_timeout = self._resolve_processing_timeout(request)
            self._record_request_start(self._current_request_id, self._current_request_method)
            worker = threading.Thread(
                target=self._handle_request,
                args=(
                    request,
                    self._current_response_path,
                    self._current_request_method,
                    self._processing_since,
                ),
                daemon=True,
            )
            worker.start()

        except json.JSONDecodeError as e:
            # A partial/corrupt request can otherwise spin forever and flood logs.
            self._log("invalid request json: %s" % str(e))
            self._record_request_finish(
                None, None, "json_error", duration_sec=0.0, error_message=str(e)
            )
            try:
                os.remove(inflight_request_path)
            except Exception:
                pass
        except Exception as e:
            self._log("error processing request: %s\n%s" % (str(e), traceback.format_exc()))
            with self._stats_lock:
                self._metrics["total_poll_errors"] += 1
            self._record_recent_error("poll_error", message=str(e))
            try:
                os.remove(inflight_request_path)
            except Exception:
                pass

    def _handle_request(self, request, response_path, request_method, request_start_time):
        """Process a single request (runs in worker thread)"""
        request_id = request.get("id")
        status = "ok"
        error_message = None
        try:
            try:
                response = self.handler.handle(request)
                if isinstance(response, dict) and "error" in response:
                    status = "error"
                    err = response.get("error")
                    if isinstance(err, dict):
                        error_message = err.get("message", "")
                    else:
                        error_message = str(err)
            except Exception as e:
                traceback.print_exc()
                status = "exception"
                error_message = str(e)
                response = {
                    "id": request_id,
                    "error": {"code": -32603, "message": str(e)}
                }

            # Only write if this request is still the current one.
            # If a timeout forced a reset and a new request was picked up,
            # writing a stale response would overwrite the new request's response.
            if self._current_request_id == request_id:
                self._write_response(response, response_path)
            else:
                status = "stale_discarded"
                if not error_message:
                    error_message = "Response arrived after request was reset/replaced"
                self._log("discarding stale response for %s (current: %s)" % (request_id, self._current_request_id))

        except Exception as e:
            status = "exception"
            error_message = str(e)
            self._log("error handling request: %s\n%s" % (str(e), traceback.format_exc()))
        finally:
            duration = max(0.0, time.time() - request_start_time) if request_start_time else 0.0
            self._record_request_finish(
                request_id,
                request_method,
                status,
                duration_sec=duration,
                error_message=error_message,
            )
            # Only clear processing state if we're still the current request
            if self._current_request_id == request_id:
                self._processing_since = None
                self._current_processing_timeout = PROCESSING_TIMEOUT
                self._current_request_id = None
                self._current_request_method = None
                self._current_response_path = RESPONSE_FILE
