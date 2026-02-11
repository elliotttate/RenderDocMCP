"""
Capture management service for RenderDoc.
"""

import os
import tempfile

import renderdoc as rd


def _trace(msg):
    try:
        d = os.path.join(tempfile.gettempdir(), "renderdoc_mcp")
        if not os.path.exists(d):
            os.makedirs(d)
        with open(os.path.join(d, "open_capture_trace.log"), "a") as f:
            f.write(msg + "\n")
    except Exception:
        pass


class CaptureManager:
    """Capture management service"""

    def __init__(self, ctx, invoke_fn):
        self.ctx = ctx
        self._invoke = invoke_fn
        self._enable_open_capture = (
            os.environ.get("RENDERDOC_MCP_ENABLE_OPEN_CAPTURE", "0").lower()
            in ("1", "true", "yes", "on")
        )

    def get_capture_status(self):
        """Check if a capture is loaded and get API info"""
        if not self.ctx.IsCaptureLoaded():
            return {"loaded": False}

        result = {"loaded": True, "api": None, "filename": None}

        try:
            result["filename"] = self.ctx.GetCaptureFilename()
        except Exception:
            pass

        # Get API type via replay
        def callback(controller):
            try:
                props = controller.GetAPIProperties()
                result["api"] = str(props.pipelineType)
            except Exception:
                pass

        self._invoke(callback)
        return result

    def list_captures(self, directory):
        """
        List all .rdc files in the specified directory.

        Args:
            directory: Directory path to search

        Returns:
            dict with 'captures' list containing file info
        """
        import os
        import datetime

        # Validate directory exists
        if not os.path.isdir(directory):
            raise ValueError("Directory not found: %s" % directory)

        captures = []

        try:
            for filename in os.listdir(directory):
                if filename.lower().endswith(".rdc"):
                    filepath = os.path.join(directory, filename)
                    if os.path.isfile(filepath):
                        stat = os.stat(filepath)
                        # Format timestamp as ISO 8601
                        mtime = datetime.datetime.fromtimestamp(stat.st_mtime)
                        captures.append({
                            "filename": filename,
                            "path": filepath,
                            "size_bytes": stat.st_size,
                            "modified_time": mtime.isoformat(),
                        })
        except Exception as e:
            raise ValueError("Failed to list directory: %s" % str(e))

        # Sort by modified time (newest first)
        captures.sort(key=lambda x: x["modified_time"], reverse=True)

        return {
            "directory": directory,
            "count": len(captures),
            "captures": captures,
        }

    def open_capture(self, capture_path):
        """
        Open a capture file in RenderDoc.

        Args:
            capture_path: Full path to the .rdc file

        Returns:
            dict with success status and capture info
        """
        _trace("open_capture called: %s" % capture_path)

        # Validate file exists
        if not os.path.isfile(capture_path):
            raise ValueError("Capture file not found: %s" % capture_path)

        # Validate extension
        if not capture_path.lower().endswith(".rdc"):
            raise ValueError("Invalid file type. Expected .rdc file: %s" % capture_path)

        # Fast path: if already loaded, avoid touching LoadCapture.
        try:
            if self.ctx.IsCaptureLoaded():
                current = self.ctx.GetCaptureFilename()
                if current:
                    cur_norm = os.path.normcase(os.path.abspath(current))
                    req_norm = os.path.normcase(os.path.abspath(capture_path))
                    if cur_norm == req_norm:
                        return {
                            "success": True,
                            "capture_path": capture_path,
                            "filename": os.path.basename(capture_path),
                            "already_loaded": True,
                        }
        except Exception:
            pass

        # Guardrail: this RenderDoc fork can hang in LoadCapture via extension API.
        # Keep this opt-in so MCP cannot freeze qrenderdoc by default.
        if not self._enable_open_capture:
            raise ValueError(
                "open_capture is disabled by default to avoid RenderDoc UI hangs on this build. "
                "Open the .rdc in qrenderdoc directly (or start qrenderdoc with the file path), "
                "then use MCP tools. To force legacy behavior set RENDERDOC_MCP_ENABLE_OPEN_CAPTURE=1."
            )

        # Create ReplayOptions with defaults
        opts = rd.ReplayOptions()

        # Try direct LoadCapture call forms. Prefer the common 4-arg signature
        # first, then fall back to 5-arg for forked builds.
        load_result = {"loaded": False, "errors": []}

        def do_load():
            # Legacy/mainline style first: (capture, opts, temporary, local)
            try:
                _trace("open_capture trying 4-arg LoadCapture")
                self.ctx.LoadCapture(
                    capture_path,
                    opts,
                    False,
                    True,
                )
                load_result["loaded"] = True
                _trace("open_capture loaded via 4-arg signature")
                return
            except Exception as e:
                msg = "4-arg LoadCapture failed: %s" % str(e)
                load_result["errors"].append(msg)
                _trace(msg)

            # Newer fork/build style fallback: (capture, opts, orig, temporary, local)
            try:
                _trace("open_capture trying 5-arg LoadCapture")
                self.ctx.LoadCapture(
                    capture_path,
                    opts,
                    capture_path,
                    False,
                    True,
                )
                load_result["loaded"] = True
                _trace("open_capture loaded via 5-arg signature")
            except Exception as e:
                msg = "5-arg LoadCapture failed: %s" % str(e)
                load_result["errors"].append(msg)
                _trace(msg)

        do_load()

        if not load_result["loaded"]:
            raise ValueError(
                "Failed to open capture: %s"
                % (" | ".join(load_result["errors"]) or "unknown error")
            )

        # Verify the capture was loaded
        if not self.ctx.IsCaptureLoaded():
            _trace("open_capture verify failed: IsCaptureLoaded() == False")
            raise ValueError("Failed to load capture (unknown error)")

        # Get capture info
        result = {
            "success": True,
            "capture_path": capture_path,
            "filename": os.path.basename(capture_path),
        }

        # Get API type if possible (may require replay thread)
        try:
            api_result = {"api": None}

            def callback(controller):
                try:
                    props = controller.GetAPIProperties()
                    api_result["api"] = str(props.pipelineType)
                except Exception:
                    pass

            self._invoke(callback)
            if api_result["api"]:
                result["api"] = api_result["api"]
        except Exception:
            pass

        _trace("open_capture complete: %s" % capture_path)
        return result
