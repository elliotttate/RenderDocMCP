"""
RenderDoc MCP Bridge Extension
Provides socket server for external MCP server communication.
"""

import os
import traceback
import tempfile

_LOG_FILE = os.path.join(tempfile.gettempdir(), "renderdoc_mcp", "extension.log")

def _log(msg):
    try:
        d = os.path.dirname(_LOG_FILE)
        if not os.path.exists(d):
            os.makedirs(d)
        with open(_LOG_FILE, "a") as f:
            f.write(msg + "\n")
    except Exception:
        pass

_log("=== Extension module loading ===")

try:
    from . import socket_server
    _log("Imported socket_server OK")
except Exception as e:
    _log("FAILED to import socket_server: %s\n%s" % (str(e), traceback.format_exc()))
    raise

try:
    from . import request_handler
    _log("Imported request_handler OK")
except Exception as e:
    _log("FAILED to import request_handler: %s\n%s" % (str(e), traceback.format_exc()))
    raise

try:
    from . import renderdoc_facade
    _log("Imported renderdoc_facade OK")
except Exception as e:
    _log("FAILED to import renderdoc_facade: %s\n%s" % (str(e), traceback.format_exc()))
    raise

# Global state
_context = None
_server = None
_version = ""

# Try to import qrenderdoc for UI integration (only available in RenderDoc)
try:
    import qrenderdoc as qrd

    _has_qrenderdoc = True
except ImportError:
    _has_qrenderdoc = False


def register(version, ctx):
    """
    Called when extension is loaded.

    Args:
        version: RenderDoc version string (e.g., "1.20")
        ctx: CaptureContext handle
    """
    global _context, _server, _version
    _log("register() called with version=%s" % version)
    try:
        _version = version
        _context = ctx

        # Create facade and handler
        facade = renderdoc_facade.RenderDocFacade(ctx)
        _log("Created RenderDocFacade")
        handler = request_handler.RequestHandler(facade)
        _log("Created RequestHandler")

        # Start socket server
        _server = socket_server.MCPBridgeServer(
            host="127.0.0.1", port=19876, handler=handler
        )
        handler.set_bridge_server(_server)
        _server.start()
        _log("Server started")

        # Register menu item if UI is available
        if _has_qrenderdoc:
            try:
                ctx.Extensions().RegisterWindowMenu(
                    qrd.WindowMenu.Tools, ["MCP Bridge", "Status"], _show_status
                )
            except Exception as e:
                _log("Could not register menu: %s" % str(e))
                print("[MCP Bridge] Could not register menu: %s" % str(e))

        _log("Extension fully loaded")
        print("[MCP Bridge] Extension loaded (RenderDoc %s)" % version)
        print("[MCP Bridge] Server listening on 127.0.0.1:19876")
    except Exception as e:
        _log("FAILED in register(): %s\n%s" % (str(e), traceback.format_exc()))
        raise


def unregister():
    """Called when extension is unloaded"""
    global _server
    _log("unregister() called")
    if _server:
        _server.stop()
        _server = None
    # Remove IPC directory to prevent stale state after RenderDoc closes
    ipc_dir = os.path.join(tempfile.gettempdir(), "renderdoc_mcp")
    try:
        import shutil
        if os.path.exists(ipc_dir):
            shutil.rmtree(ipc_dir, ignore_errors=True)
    except Exception:
        pass
    print("[MCP Bridge] Extension unloaded")


def _show_status(ctx, data):
    """Show status dialog"""
    if _server and _server.is_running():
        ctx.Extensions().MessageDialog(
            "MCP Bridge is running on port 19876", "MCP Bridge Status"
        )
    else:
        ctx.Extensions().ErrorDialog("MCP Bridge is not running", "MCP Bridge Status")
