# RenderDoc MCP: LLM-Actionable Data Build Plan

## Objective

Make RenderDoc captures usable by an LLM with:
- deterministic schemas
- bounded payloads
- explicit diagnostics for transport health
- reliable drill-down paths that do not require guesswork

This plan is execution-first: every phase has concrete APIs, validation, and release criteria.

## Why MCP "Drops" Hurt LLM Workflows

When transport drops or times out, an LLM loses conversational state and retries blind.
The bridge must therefore expose:
- queue/inflight health
- last failures
- request latency behavior

Without this, the model cannot distinguish:
- "capture is huge, still processing"
- "extension hung"
- "request got overwritten"

## Target Architecture

1. Transport layer (file IPC)
- per-request spool files (`request.<id>.json`, `response.<id>.json`)
- heartbeat + diagnostics snapshot
- explicit request lifecycle metrics

2. Data layer (RenderDoc extraction)
- Tier A: compact analysis-first endpoints
- Tier B: raw drill-down endpoints

3. Agent workflow layer
- deterministic call sequence
- bounded expansion policy
- schema-versioned outputs

## Endpoint Strategy

### Tier A (LLM-first, high signal)

1. `get_bridge_diagnostics`
- Purpose: explain drops/timeouts immediately
- Must include:
  - heartbeat age
  - queue depth / oldest pending age
  - active request + elapsed/timeout
  - aggregate error/timeouts counters
  - recent error list (bounded)

2. `get_frame_summary`
- Purpose: quick frame-level orientation

3. `get_event_insight`
- Purpose: one-call event triage bundle
- Must include:
  - action metadata + marker path
  - stage/resource bounded previews
  - outputs + IA summary
  - heuristic findings
  - recommended next calls

4. `get_frame_digest`
- Purpose: frame-level "hotspot" shortlist
- Implemented fields:
  - top timing events
  - marker-level summaries
  - anomaly list with severity/code/message
  - ranked investigation sequence (`recommended_next_calls`)

### Tier B (drill-down)

Keep existing granular APIs:
- `get_pipeline_state`
- `get_shader_info`
- `get_texture_info` / `get_texture_data`
- `get_buffer_contents`
- search endpoints

Rule: Tier B should never be required for first-pass diagnosis.

## Schema Contract Rules

1. Every Tier A response must include `schema_version`.
2. Lists must have bounded default preview sizes.
3. Truncation must be explicit (`truncated=true`, count fields).
4. Error payloads must be structured and stable:
- `code`
- `message`
- optional `context`

5. Do not return renderer-internal opaque blobs unless an explicit debug flag is set.

## Payload Budget

Default response targets:
- `get_bridge_diagnostics`: < 8 KB
- `get_frame_summary`: < 32 KB
- `get_event_insight`: < 120 KB
- `get_frame_digest`: < 160 KB

Hard policy:
- disassembly/constants are opt-in
- large arrays should expose `count + preview + truncated`

## LLM Call Flow (Recommended)

1. `get_bridge_diagnostics`
- If unhealthy, stop and remediate transport first.

2. `get_capture_status`
- Verify capture loaded + API.

3. `get_frame_summary`
- Establish scope.

4. `get_action_timings(top_n=20)` when available.

5. For selected events:
- `get_event_insight(event_id=...)`

6. Drill down selectively:
- `get_pipeline_state`
- `get_shader_info`
- resource reads only when justified

## Reliability Validation Plan

### Phase 1: transport baseline

Done/required:
- per-request spool queue
- atomic writes
- stale-response protection
- heartbeat monitoring

Validation:
- multi-thread stress: 200+ calls, 0 dropped responses
- mixed method stress including long calls

### Phase 2: observability

Required:
- `get_bridge_diagnostics`
- request lifecycle counters
- recent errors ring buffer
- diagnostics snapshot file for postmortem

Validation:
- force timeout path and verify counters/errors update correctly
- verify diagnostics still updates while long request is running

### Phase 3: schema stability

Required:
- golden JSON shape tests for Tier A endpoints
- backwards-compatible schema updates via new `schema_version`

Validation:
- CI-style snapshot checks against known capture samples

### Phase 4: actionability

Required:
- every Tier A response includes concrete next-call suggestions
- heuristic findings have severity + code + message

Validation:
- scripted "agent loop" can localize common issues in <= 6 calls

## Build/Release Workflow

Per change-set:
1. `python -m compileall mcp_server renderdoc_extension`
2. Reinstall extension (`python scripts/install_extension.py`)
3. Restart RenderDoc
4. Run stress:
- `python scripts/stress_bridge.py --threads 8 --requests 200 --dump-diagnostics`
5. Smoke test with capture:
- `get_capture_status`
- `get_frame_summary`
- `get_event_insight(event_id=...)`

Release gate:
- no dropped responses in stress run
- diagnostics endpoint operational
- schema snapshots unchanged or intentionally versioned

## Near-Term Implementation Backlog

1. Add machine-readable diagnostic `status` enum (`healthy/degraded/stalled`).
2. Add optional resource "importance ranking" in `get_event_insight`.
3. Add regression fixtures for D3D11 + Vulkan captures.
4. Add one-command reliability CI script (stress + schema checks).
