# RenderDoc MCP Server

An MCP server that runs as a RenderDoc UI extension. It allows AI assistants to access RenderDoc capture data and assist with DirectX 11/12 graphics debugging.

## Architecture

**Hybrid Process Isolation**:

```
Claude/AI Client (stdio)
        │
        ▼
MCP Server Process (Standard Python + FastMCP 2.0)
        │ File-based IPC (%TEMP%/renderdoc_mcp/)
        ▼
RenderDoc Process (Extension + File Polling)
```

## Project Structure

```
RenderDocMCP/
├── mcp_server/                        # MCP Server
│   ├── server.py                      # FastMCP entry point
│   ├── config.py                      # Configuration
│   └── bridge/
│       └── client.py                  # File-based IPC client
│
├── renderdoc_extension/               # RenderDoc Extension
│   ├── __init__.py                    # register()/unregister()
│   ├── extension.json                 # Manifest
│   ├── socket_server.py               # File-based IPC server
│   ├── request_handler.py             # Request handler
│   └── renderdoc_facade.py            # RenderDoc API wrapper
│
└── scripts/
    └── install_extension.py           # Extension installer
```

## MCP Tools

| Tool Name | Description |
|-----------|-------------|
| `list_captures` | List .rdc files in a specified directory |
| `open_capture` | Open a capture file (existing capture is automatically closed) |
| `get_capture_status` | Check capture load status |
| `get_draw_calls` | List draw calls (hierarchical, with filtering support) |
| `get_frame_summary` | Frame-level statistics (draw call count, marker list, etc.) |
| `find_draws_by_shader` | Reverse-lookup draw calls by shader name |
| `find_draws_by_texture` | Reverse-lookup draw calls by texture name |
| `find_draws_by_resource` | Reverse-lookup draw calls by resource ID |
| `get_draw_call_details` | Detailed info for a specific draw call |
| `get_action_timings` | Get GPU execution time for actions |
| `get_shader_info` | Shader source / constant buffers |
| `get_buffer_contents` | Get buffer data (offset/length supported) |
| `get_texture_info` | Texture metadata |
| `get_texture_data` | Get texture pixel data (mip/slice/3D slice supported) |
| `get_pipeline_state` | Full pipeline state |

### get_draw_calls Filtering Options

```python
get_draw_calls(
    include_children=True,      # Include child actions
    marker_filter="Camera.Render",  # Only get actions under this marker
    exclude_markers=["GUI.Repaint", "UIR.DrawChain"],  # Markers to exclude
    event_id_min=7372,          # Start of event_id range
    event_id_max=7600,          # End of event_id range
    only_actions=True,          # Exclude markers (draw calls only)
    flags_filter=["Drawcall", "Dispatch"],  # Specific flags only
)
```

### Capture Management Tools

```python
# List capture files in a directory
list_captures(directory="D:\\captures")
# → {"count": 3, "captures": [{"filename": "game.rdc", "path": "...", "size_bytes": 12345, "modified_time": "..."}, ...]}

# Open a capture file (existing capture is automatically closed)
open_capture(capture_path="D:\\captures\\game.rdc")
# → {"success": true, "filename": "game.rdc", "api": "D3D11"}
```

### Reverse-Lookup Search Tools

```python
# Search by shader name (partial match)
find_draws_by_shader(shader_name="Toon", stage="pixel")

# Search by texture name (partial match)
find_draws_by_texture(texture_name="CharacterSkin")

# Search by resource ID (exact match)
find_draws_by_resource(resource_id="ResourceId::12345")
```

### GPU Timing Retrieval

```python
# Get timings for all actions
get_action_timings()
# → {"available": true, "unit": "CounterUnit.Seconds", "timings": [...], "total_duration_ms": 12.5, "count": 150}

# Get timings for specific event IDs only
get_action_timings(event_ids=[100, 200, 300])

# Filter by marker
get_action_timings(marker_filter="Camera.Render", exclude_markers=["GUI.Repaint"])
```

**Note**: GPU timing counters may not be available depending on hardware/driver.
If `available: false` is returned, timing information cannot be retrieved for that capture.

## Communication Protocol

File-based IPC:
- IPC directory: `%TEMP%/renderdoc_mcp/`
- `request.json`: Request (MCP Server → RenderDoc)
- `response.json`: Response (RenderDoc → MCP Server)
- `lock`: Write lock file
- Polling interval: 100ms (RenderDoc side)

## Development Notes

- File-based IPC is used because RenderDoc's built-in Python lacks socket/QtNetwork modules
- The RenderDoc extension uses only the Python 3.6 standard library
- ReplayController access is done via `BlockInvoke`

## Reference Links

- [FastMCP](https://github.com/jlowin/fastmcp)
- [RenderDoc Python API](https://renderdoc.org/docs/python_api/index.html)
- [RenderDoc Extension Registration](https://renderdoc.org/docs/how/how_python_extension.html)
