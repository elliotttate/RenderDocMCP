# RenderDoc MCP Server

An MCP server that runs as a RenderDoc UI extension. It allows AI assistants to access RenderDoc capture data and assist with graphics debugging.

## Architecture

```
Claude/AI Client (stdio)
        │
        ▼
MCP Server Process (Python + FastMCP 2.0)
        │ File-based IPC (%TEMP%/renderdoc_mcp/)
        ▼
RenderDoc Process (Extension)
```

File-based IPC is used because RenderDoc's built-in Python lacks the socket module.

## Stability Tuning (MCP Disconnect Mitigation)

If disconnections occur during long `open_capture` calls or large queries, the following environment variables can be adjusted:

- `RENDERDOC_MCP_TIMEOUT` (default: `180`)
  Default wait time in seconds on the MCP client side
- `RENDERDOC_MCP_TIMEOUT_OPEN_CAPTURE` (default: `420`)
  Wait time in seconds specifically for `open_capture`
- `RENDERDOC_MCP_HEARTBEAT_MAX_AGE` (default: `30`)
  Seconds before heartbeat is considered "stale" (for diagnostics)
- `RENDERDOC_MCP_HEARTBEAT_STARTUP_GRACE` (default: `8`)
  Grace period in seconds to wait for heartbeat after RenderDoc startup
- `RENDERDOC_MCP_HEARTBEAT_MISSING_FAIL_FAST` (default: `10`)
  Seconds for early failure when heartbeat fail-fast is enabled
- `RENDERDOC_MCP_HEARTBEAT_FAIL_FAST_DURING_REQUEST` (default: `0`)
  Set to `1/true` to treat heartbeat loss during a pending request as early failure (disabled by default as it may produce false positives during long API calls)
- `RENDERDOC_MCP_HARD_TIMEOUT_CAP` (default: `0`)
  Upper limit for each request timeout (`0` to disable). Disabled by default to avoid interrupting long-running operations
- `RENDERDOC_MCP_PROCESSING_TIMEOUT` (default: `420`)
  Maximum processing time in seconds allowed per request on the RenderDoc extension side
- `RENDERDOC_MCP_HEARTBEAT_INTERVAL` (default: `1`)
  Interval in seconds at which the RenderDoc extension writes the heartbeat file
- `RENDERDOC_MCP_CLIENT_MUTEX_STALE_AGE` (default: `900`)
  Seconds before the client exclusive lock is considered stale (increased for long-running operations)

## Setup

### 1. Install the RenderDoc Extension

```bash
python scripts/install_extension.py
```

The extension is installed to `%APPDATA%\qrenderdoc\extensions\renderdoc_mcp_bridge`.

### 2. Enable the Extension in RenderDoc

1. Launch RenderDoc
2. Go to Tools > Manage Extensions
3. Enable "RenderDoc MCP Bridge"

### 3. Install the MCP Server

```bash
uv tool install
uv tool update-shell  # Add to PATH
```

After restarting the shell, the `renderdoc-mcp` command becomes available.

> **Note**: Adding `--editable` makes source code changes take effect immediately (useful during development).
> For a stable installation, use `uv tool install .`.

### 4. Configure the MCP Client

#### Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "renderdoc": {
      "command": "renderdoc-mcp"
    }
  }
}
```

#### Claude Code

Add to `.mcp.json`:

```json
{
  "mcpServers": {
    "renderdoc": {
      "command": "renderdoc-mcp"
    }
  }
}
```

## Usage

1. Launch RenderDoc and open a capture file (.rdc)
2. Access RenderDoc data from an MCP client (e.g. Claude)

## MCP Tools

| Tool | Description |
|------|-------------|
| `get_bridge_diagnostics` | Get MCP bridge queue/heartbeat/recent errors (for disconnect investigation) |
| `get_capture_status` | Check capture load status |
| `get_draw_calls` | Get draw calls in a hierarchical structure |
| `get_draw_call_details` | Get detailed information for a specific draw call |
| `get_shader_info` | Get shader source code and constant buffer values |
| `get_buffer_contents` | Get buffer contents (Base64) |
| `get_texture_info` | Get texture metadata |
| `get_texture_data` | Get texture pixel data (Base64) |
| `get_pipeline_state` | Get pipeline state |
| `get_event_insight` | Get a compact LLM-oriented event analysis snapshot |
| `get_frame_digest` | Get a frame-level summary of hotspots/markers/anomaly candidates |

## Examples

### Get Draw Call List

```
get_draw_calls(include_children=true)
```

### Get Shader Information

```
get_shader_info(event_id=123, stage="pixel")
```

### Get Pipeline State

```
get_pipeline_state(event_id=123)
```

### LLM-Oriented Event Analysis Snapshot

```
get_event_insight(event_id=123)
```

Optionally enable disassembly/constant values:

```
get_event_insight(
  event_id=123,
  include_shader_disassembly=true,
  include_shader_constants=true,
  max_resources_per_stage=12
)
```

### LLM-Oriented Frame Summary (Recommended Starting Point)

```
get_frame_digest(
  max_hotspots=12,
  max_markers=12,
  include_event_insights=true,
  event_insight_budget=3
)
```

### Bridge Disconnect Diagnostics

```
get_bridge_diagnostics()
```

If `queue.pending_count` keeps increasing or `processing.active=true` with `elapsed_sec` keeps growing,
it suggests the RenderDoc extension is stalled.
Even when RenderDoc is unresponsive, a recent snapshot is saved to
`%TEMP%/renderdoc_mcp/bridge_diagnostics.json`.

## LLM-Oriented Design Roadmap

The data contracts and extraction strategies for enabling LLMs to reason accurately with minimal round-trips
are documented in `docs/LLM_DATA_PLAN.md`.

### Get Texture Data

```
# Get mip 0 of a 2D texture
get_texture_data(resource_id="ResourceId::123")

# Get a specific mip level
get_texture_data(resource_id="ResourceId::123", mip=2)

# Get a specific cube map face (0=X+, 1=X-, 2=Y+, 3=Y-, 4=Z+, 5=Z-)
get_texture_data(resource_id="ResourceId::456", slice=3)

# Get a specific depth slice of a 3D texture
get_texture_data(resource_id="ResourceId::789", depth_slice=5)
```

### Get Partial Buffer Data

```
# Get entire buffer
get_buffer_contents(resource_id="ResourceId::123")

# Get 512 bytes starting at offset 256
get_buffer_contents(resource_id="ResourceId::123", offset=256, length=512)
```

## Requirements

- Python 3.10+
- [uv](https://docs.astral.sh/uv/)
- RenderDoc 1.20+

> **Note**: Testing has only been done on Windows + DirectX 11.
> It may work on Linux/macOS + Vulkan/OpenGL, but this has not been verified.

## License

MIT
