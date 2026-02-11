"""
RenderDoc MCP Server
FastMCP 2.0 server providing access to RenderDoc capture data.
"""

import json
import os
import tempfile
from typing import Literal

from fastmcp import FastMCP

from .bridge.client import RenderDocBridge, RenderDocBridgeError
from .config import settings

# Initialize FastMCP server
mcp = FastMCP(
    name="RenderDoc MCP Server",
)

# RenderDoc bridge client
bridge = RenderDocBridge(host=settings.renderdoc_host, port=settings.renderdoc_port)


@mcp.tool
def get_capture_status() -> dict:
    """
    Check if a capture is currently loaded in RenderDoc.
    Returns the capture status and API type if loaded.
    """
    return bridge.call("get_capture_status")


@mcp.tool
def get_bridge_diagnostics(
    include_recent_errors: bool = True,
    max_recent_errors: int = 16,
) -> dict:
    """
    Get bridge transport diagnostics for drop/timeout triage.

    This reports queue health, in-flight request state, heartbeat age,
    and recent bridge errors from the RenderDoc extension process.
    """
    try:
        return bridge.call(
            "get_bridge_diagnostics",
            {
                "include_recent_errors": include_recent_errors,
                "max_recent_errors": max_recent_errors,
            },
        )
    except RenderDocBridgeError as exc:
        # Fallback when extension is unhealthy: return the last diagnostics
        # snapshot from disk so an LLM can still reason about failure mode.
        snapshot_path = os.path.join(
            tempfile.gettempdir(), "renderdoc_mcp", "bridge_diagnostics.json"
        )
        snapshot = None
        if os.path.exists(snapshot_path):
            try:
                with open(snapshot_path, "r", encoding="utf-8") as f:
                    snapshot = json.load(f)
            except Exception:
                snapshot = None
        return {
            "schema_version": "bridge_diagnostics.v1",
            "running": False,
            "transport_error": str(exc),
            "snapshot_path": snapshot_path,
            "snapshot": snapshot,
        }


@mcp.tool
def get_draw_calls(
    include_children: bool = True,
    marker_filter: str | None = None,
    exclude_markers: list[str] | None = None,
    event_id_min: int | None = None,
    event_id_max: int | None = None,
    only_actions: bool = False,
    flags_filter: list[str] | None = None,
) -> dict:
    """
    Get the list of all draw calls and actions in the current capture.

    Args:
        include_children: Include child actions in the hierarchy (default: True)
        marker_filter: Only include actions under markers containing this string (partial match)
        exclude_markers: Exclude actions under markers containing these strings (list of partial matches)
        event_id_min: Only include actions with event_id >= this value
        event_id_max: Only include actions with event_id <= this value
        only_actions: If True, exclude marker actions (PushMarker/PopMarker/SetMarker)
        flags_filter: Only include actions with these flags (list of flag names, e.g. ["Drawcall", "Dispatch"])

    Returns a hierarchical tree of actions including markers, draw calls,
    dispatches, and other GPU events.
    """
    params: dict[str, object] = {"include_children": include_children}
    if marker_filter is not None:
        params["marker_filter"] = marker_filter
    if exclude_markers is not None:
        params["exclude_markers"] = exclude_markers
    if event_id_min is not None:
        params["event_id_min"] = event_id_min
    if event_id_max is not None:
        params["event_id_max"] = event_id_max
    if only_actions:
        params["only_actions"] = only_actions
    if flags_filter is not None:
        params["flags_filter"] = flags_filter
    return bridge.call("get_draw_calls", params)


@mcp.tool
def get_frame_summary() -> dict:
    """
    Get a summary of the current capture frame.

    Returns statistics about the frame including:
    - API type (D3D11, D3D12, Vulkan, etc.)
    - Total action count
    - Statistics: draw calls, dispatches, clears, copies, presents, markers
    - Top-level markers with event IDs and child counts
    - Resource counts: textures, buffers
    """
    return bridge.call("get_frame_summary")


@mcp.tool
def find_draws_by_shader(
    shader_name: str,
    stage: Literal["vertex", "hull", "domain", "geometry", "pixel", "compute"] | None = None,
    max_results: int = 0,
) -> dict:
    """
    Find all draw calls using a shader with the given name (partial match).

    Args:
        shader_name: Partial name to search for in shader names or entry points
        stage: Optional shader stage to search (if not specified, searches all stages)
        max_results: Stop after finding this many matches (0 = unlimited, default)

    Returns a list of matching draw calls with event IDs and match reasons.
    """
    params: dict[str, object] = {"shader_name": shader_name}
    if stage is not None:
        params["stage"] = stage
    if max_results > 0:
        params["max_results"] = max_results
    return bridge.call("find_draws_by_shader", params)


@mcp.tool
def find_draws_by_texture(
    texture_name: str,
    max_results: int = 0,
) -> dict:
    """
    Find all draw calls using a texture with the given name (partial match).

    Args:
        texture_name: Partial name to search for in texture resource names
        max_results: Stop after finding this many matches (0 = unlimited, default)

    Returns a list of matching draw calls with event IDs and match reasons.
    Searches SRVs, UAVs, and render targets.
    """
    params: dict[str, object] = {"texture_name": texture_name}
    if max_results > 0:
        params["max_results"] = max_results
    return bridge.call("find_draws_by_texture", params)


@mcp.tool
def find_draws_by_resource(
    resource_id: str,
    max_results: int = 0,
) -> dict:
    """
    Find all draw calls using a specific resource ID (exact match).

    Args:
        resource_id: Resource ID to search for (e.g. "ResourceId::12345" or "12345")
        max_results: Stop after finding this many matches (0 = unlimited, default)

    Returns a list of matching draw calls with event IDs and match reasons.
    Searches shaders, SRVs, UAVs, render targets, and depth targets.
    """
    params: dict[str, object] = {"resource_id": resource_id}
    if max_results > 0:
        params["max_results"] = max_results
    return bridge.call("find_draws_by_resource", params)


@mcp.tool
def get_draw_call_details(event_id: int) -> dict:
    """
    Get detailed information about a specific draw call.

    Args:
        event_id: The event ID of the draw call to inspect

    Includes vertex/index counts, resource outputs, and other metadata.
    """
    return bridge.call("get_draw_call_details", {"event_id": event_id})


@mcp.tool
def get_action_timings(
    event_ids: list[int] | None = None,
    marker_filter: str | None = None,
    exclude_markers: list[str] | None = None,
    top_n: int = 0,
) -> dict:
    """
    Get GPU timing information for actions (draw calls, dispatches, etc.).

    Args:
        event_ids: Optional list of specific event IDs to get timings for.
                   If not specified, returns timings for all actions.
        marker_filter: Only include actions under markers containing this string (partial match).
        exclude_markers: Exclude actions under markers containing these strings.
        top_n: If > 0, return only the N slowest actions sorted by duration descending.

    Returns timing data including:
    - available: Whether GPU timing counters are supported
    - unit: Time unit (typically "seconds")
    - timings: List of {event_id, name, duration_seconds, duration_ms}
    - total_duration_ms: Sum of all durations
    - count: Number of timing entries

    Note: GPU timing counters may not be available on all hardware/drivers.
    """
    params: dict[str, object] = {}
    if event_ids is not None:
        params["event_ids"] = event_ids
    if marker_filter is not None:
        params["marker_filter"] = marker_filter
    if exclude_markers is not None:
        params["exclude_markers"] = exclude_markers
    if top_n > 0:
        params["top_n"] = top_n
    return bridge.call("get_action_timings", params)


@mcp.tool
def get_shader_info(
    event_id: int,
    stage: Literal["vertex", "hull", "domain", "geometry", "pixel", "compute"],
) -> dict:
    """
    Get shader information for a specific stage at a given event.

    Args:
        event_id: The event ID to inspect the shader at
        stage: The shader stage (vertex, hull, domain, geometry, pixel, compute)

    Returns shader disassembly, constant buffer values, and resource bindings.
    """
    return bridge.call("get_shader_info", {"event_id": event_id, "stage": stage})


@mcp.tool
def get_buffer_contents(
    resource_id: str,
    offset: int = 0,
    length: int = 0,
    event_id: int | None = None,
) -> dict:
    """
    Read the contents of a buffer resource.

    Args:
        resource_id: The resource ID of the buffer to read
        offset: Byte offset to start reading from (default: 0)
        length: Number of bytes to read, 0 for entire buffer (default: 0)

    Returns buffer data as base64-encoded bytes along with metadata.
    """
    params: dict[str, object] = {
        "resource_id": resource_id,
        "offset": offset,
        "length": length,
    }
    if event_id is not None:
        params["event_id"] = event_id
    return bridge.call("get_buffer_contents", params)


@mcp.tool
def get_texture_info(resource_id: str) -> dict:
    """
    Get metadata about a texture resource.

    Args:
        resource_id: The resource ID of the texture

    Includes dimensions, format, mip levels, and other properties.
    """
    return bridge.call("get_texture_info", {"resource_id": resource_id})


@mcp.tool
def get_texture_data(
    resource_id: str,
    mip: int = 0,
    slice: int = 0,
    sample: int = 0,
    depth_slice: int | None = None,
    event_id: int | None = None,
) -> dict:
    """
    Read the pixel data of a texture resource.

    Args:
        resource_id: The resource ID of the texture to read
        mip: Mip level to retrieve (default: 0)
        slice: Array slice or cube face index (default: 0)
               For cube maps: 0=X+, 1=X-, 2=Y+, 3=Y-, 4=Z+, 5=Z-
        sample: MSAA sample index (default: 0)
        depth_slice: For 3D textures only, extract a specific depth slice (default: None = full volume)
                     When specified, returns only the 2D slice at that depth index

    Returns texture pixel data as base64-encoded bytes along with metadata
    including dimensions at the requested mip level and format information.
    """
    params = {"resource_id": resource_id, "mip": mip, "slice": slice, "sample": sample}
    if depth_slice is not None:
        params["depth_slice"] = depth_slice
    if event_id is not None:
        params["event_id"] = event_id
    return bridge.call("get_texture_data", params)


@mcp.tool
def get_pipeline_state(event_id: int) -> dict:
    """
    Get the full graphics pipeline state at a specific event.

    Args:
        event_id: The event ID to get pipeline state at

    Returns detailed pipeline state including:
    - Bound shaders with entry points for each stage
    - Shader resources (SRVs): textures and buffers with dimensions, format, slot, name
    - UAVs (RWTextures/RWBuffers): resource details with dimensions and format
    - Samplers: addressing modes, filter settings, LOD parameters
    - Constant buffers: slot, size, variable count
    - Render targets and depth target
    - Viewports and input assembly state
    """
    return bridge.call("get_pipeline_state", {"event_id": event_id})


@mcp.tool
def get_event_insight(
    event_id: int,
    include_shader_disassembly: bool = False,
    include_shader_constants: bool = False,
    max_resources_per_stage: int = 8,
    max_cbuffer_variables: int = 24,
    disassembly_char_limit: int = 24000,
) -> dict:
    """
    Get a compact, high-signal analysis snapshot for a specific event.

    This is an LLM-oriented endpoint that bundles:
    - Action metadata
    - Marker context path
    - Input assembly summary
    - Render targets/depth outputs
    - Per-stage shader/resource previews (bounded size)
    - Heuristics and recommended follow-up calls

    Args:
        event_id: The event ID to inspect
        include_shader_disassembly: Include shader disassembly text (default: False)
        include_shader_constants: Include constant buffer values (default: False)
        max_resources_per_stage: Preview cap for SRVs/UAVs/samplers/cbuffers
        max_cbuffer_variables: Preview cap for cbuffer variables
        disassembly_char_limit: Max disassembly characters per stage
    """
    return bridge.call(
        "get_event_insight",
        {
            "event_id": event_id,
            "include_shader_disassembly": include_shader_disassembly,
            "include_shader_constants": include_shader_constants,
            "max_resources_per_stage": max_resources_per_stage,
            "max_cbuffer_variables": max_cbuffer_variables,
            "disassembly_char_limit": disassembly_char_limit,
        },
    )


@mcp.tool
def get_frame_digest(
    max_hotspots: int = 12,
    max_markers: int = 12,
    marker_filter: str | None = None,
    event_id_min: int | None = None,
    event_id_max: int | None = None,
    include_event_insights: bool = False,
    event_insight_budget: int = 3,
    max_resources_per_stage: int = 8,
) -> dict:
    """
    Get a compact frame-level digest for LLM triage.

    Bundles frame statistics, timing hotspots, marker summaries, and
    high-priority next calls in one bounded response.
    """
    params: dict[str, object] = {
        "max_hotspots": max_hotspots,
        "max_markers": max_markers,
        "include_event_insights": include_event_insights,
        "event_insight_budget": event_insight_budget,
        "max_resources_per_stage": max_resources_per_stage,
    }
    if marker_filter is not None:
        params["marker_filter"] = marker_filter
    if event_id_min is not None:
        params["event_id_min"] = event_id_min
    if event_id_max is not None:
        params["event_id_max"] = event_id_max
    return bridge.call("get_frame_digest", params)


@mcp.tool
def list_captures(directory: str) -> dict:
    """
    List all RenderDoc capture files (.rdc) in the specified directory.

    Args:
        directory: The directory path to search for capture files

    Returns a list of capture files with their metadata including:
    - filename: The capture file name
    - path: Full path to the file
    - size_bytes: File size in bytes
    - modified_time: Last modified timestamp (ISO format)
    """
    return bridge.call("list_captures", {"directory": directory})


@mcp.tool
def open_capture(capture_path: str) -> dict:
    """
    Open a RenderDoc capture file (.rdc).

    Args:
        capture_path: Full path to the capture file to open

    Returns success status and information about the opened capture.
    Note: This will close any currently open capture.

    Safety:
    - On some RenderDoc forks the extension-side LoadCapture API can hang.
      The extension keeps open_capture disabled by default unless
      `RENDERDOC_MCP_ENABLE_OPEN_CAPTURE=1` is set in the RenderDoc process.
    """
    return bridge.call("open_capture", {"capture_path": capture_path})


def main():
    """Run the MCP server"""
    # Important for stdio MCP transports: prevent startup banner text from
    # being written to stdout, which can corrupt JSON-RPC framing.
    mcp.run(show_banner=False, log_level="error")


if __name__ == "__main__":
    main()
