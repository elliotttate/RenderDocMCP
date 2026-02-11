# RenderDoc MCP Improvement Proposal

## Background

When analyzing RenderDoc captures from Unity projects, the following issues arise:

1. **UI Noise Problem**: Captures from the Unity Editor contain large amounts of Editor UI rendering such as `GUI.Repaint` and `UIR.DrawChain`, making it difficult to find the actual game rendering (under `Camera.Render`)
2. **Response Size Problem**: The result of `get_draw_calls(include_children=true)` exceeds 70KB, consuming significant LLM context
3. **Exploration Inefficiency**: Finding draw calls that use a specific shader or texture requires inspecting every draw call one by one

## Improvement Proposals

### 1. Marker Filtering (Priority: High)

A feature to retrieve only actions under a specific marker, or to exclude specific markers.

```python
get_draw_calls(
    include_children=True,
    marker_filter="Camera.Render",  # Only get actions under this marker
    exclude_markers=["GUI.Repaint", "UIR.DrawChain", "UGUI.Rendering"]
)
```

**Use Cases**:
- Extract only game rendering from Unity Editor captures
- Investigate specific rendering passes only (Shadows, PostProcess, etc.)

**Expected Benefits**:
- Reduce response size to 10-20% of original
- Fits within a size directly parseable by LLMs

---

### 2. Event ID Range Specification (Priority: High)

A feature to retrieve only actions within a specific event_id range.

```python
get_draw_calls(
    event_id_min=7372,
    event_id_max=7600,
    include_children=True
)
```

**Use Cases**:
- When the event_id of `Camera.Render` is known, retrieve only its surrounding area
- Detailed investigation around a problematic draw call

**Expected Benefits**:
- Fast retrieval of only the necessary portion
- Enables incremental exploration

---

### 3. Reverse-Lookup Search by Shader/Texture/Resource (Priority: Medium)

A feature to search for draw calls that use a specific resource.

```python
# Search by shader name (partial match)
find_draws_by_shader(shader_name="Toon")

# Search by texture name (partial match)
find_draws_by_texture(texture_name="CharacterSkin")

# Search by resource ID (exact match)
find_draws_by_resource(resource_id="ResourceId::12345")
```

**Example Return Value**:
```json
{
  "matches": [
    {"event_id": 7538, "name": "DrawIndexed", "match_reason": "pixel_shader contains 'Toon'"},
    {"event_id": 7620, "name": "DrawIndexed", "match_reason": "pixel_shader contains 'Toon'"}
  ],
  "total_matches": 2
}
```

**Use Cases**:
- Directly answer the most common question: "Which draws use this shader?"
- Track where a specific texture is used
- Determine the impact scope of a shader bug

---

### 4. Frame Summary Retrieval (Priority: Medium)

A feature to get an overview of the entire frame.

```python
get_frame_summary()
```

**Example Return Value**:
```json
{
  "api": "D3D11",
  "total_events": 7763,
  "statistics": {
    "draw_calls": 64,
    "dispatches": 193,
    "clears": 5,
    "copies": 8
  },
  "top_level_markers": [
    {"name": "WaitForRenderJobs", "event_id": 118},
    {"name": "CustomRenderTextures.Update", "event_id": 6451},
    {"name": "Camera.Render", "event_id": 7372},
    {"name": "UIR.DrawChain", "event_id": 6484}
  ],
  "render_targets": [
    {"resource_id": "ResourceId::22573", "name": "MainRT", "resolution": "1920x1080"},
    {"resource_id": "ResourceId::22585", "name": "ShadowMap", "resolution": "2048x2048"}
  ],
  "unique_shaders": {
    "vertex": 12,
    "pixel": 15,
    "compute": 8
  }
}
```

**Use Cases**:
- Get the big picture as a starting point for exploration
- Decide which marker subtree to investigate in detail
- Get a performance overview

---

### 5. Draw Calls Only Mode (Priority: Medium)

A feature to exclude markers (PushMarker/PopMarker) and retrieve only actual draw calls.

```python
get_draw_calls(
    only_actions=True,  # Exclude markers
    flags_filter=["Drawcall", "Dispatch"]  # Only items with specific flags
)
```

**Use Cases**:
- When you just want the total count and list of draw calls
- When investigating Compute Shaders (Dispatch) only

---

### 6. Batch Pipeline State Retrieval (Priority: Low)

A feature to retrieve pipeline states for multiple event_ids at once.

```python
get_multiple_pipeline_states(event_ids=[7538, 7558, 7450, 7458])
```

**Example Return Value**:
```json
{
  "states": {
    "7538": { /* pipeline state */ },
    "7558": { /* pipeline state */ },
    "7450": { /* pipeline state */ },
    "7458": { /* pipeline state */ }
  }
}
```

**Use Cases**:
- Comparative analysis of multiple draw calls
- Difference investigation (comparing a normal draw call with an abnormal one)

---

## Priority Summary

| Priority | Feature | Implementation Difficulty | Impact |
|----------|---------|--------------------------|--------|
| **High** | Marker filtering | Medium | Dramatic improvement by removing UI noise |
| **High** | Event ID range specification | Low | Faster via partial retrieval |
| **Medium** | Shader/texture reverse-lookup | High | Directly supports the most common use case |
| **Medium** | Frame summary | Medium | Useful as an exploration starting point |
| **Medium** | Draw calls only mode | Low | Simple filtering |
| **Low** | Batch retrieval | Low | Efficiency improvement, not essential |

## Unity-Specific Filtering Presets (Optional)

Unity-specific presets would be convenient:

```python
get_draw_calls(
    preset="unity_game_rendering"
)
```

**Preset Contents**:
- `marker_filter`: "Camera.Render"
- `exclude_markers`: ["GUI.Repaint", "UIR.DrawChain", "GUITexture.Draw", "UGUI.Rendering.RenderOverlays", "PlayerEndOfFrame", "EditorLoop"]

---

## Reference: Current Workflow Issues

### Current Flow

```
1. get_draw_calls(include_children=true)
   → Returns 76KB of JSON (saved to file)

2. Analyze the file with external tools (Python, etc.)
   → Identify the event_id of Camera.Render (e.g., 7372)

3. Manually specify event_id range for detailed investigation
   → get_pipeline_state(7538), get_shader_info(7538, "pixel"), ...
```

### Ideal Flow After Improvements

```
1. get_frame_summary()
   → Learn that Camera.Render is at event_id: 7372

2. get_draw_calls(marker_filter="Camera.Render", exclude_markers=[...])
   → Get only the necessary draw calls (a few KB)

3. find_draws_by_shader(shader_name="MyShader")
   → Directly get the matching event_ids

4. get_pipeline_state(event_id) for detailed inspection
```

---

## Appendix: Unity Markers to Skip

Markers to exclude when working with Unity Editor captures:

| Marker Name | Description |
|-------------|-------------|
| `GUI.Repaint` | IMGUI rendering |
| `UIR.DrawChain` | UI Toolkit rendering |
| `GUITexture.Draw` | GUI texture rendering |
| `UGUI.Rendering.RenderOverlays` | uGUI overlay rendering |
| `PlayerEndOfFrame` | End-of-frame processing |
| `EditorLoop` | Editor loop processing |

Conversely, important markers:

| Marker Name | Description |
|-------------|-------------|
| `Camera.Render` | Main camera rendering entry point |
| `Drawing` | Drawing phase |
| `Render.OpaqueGeometry` | Opaque object rendering |
| `Render.TransparentGeometry` | Transparent object rendering |
| `RenderForward.RenderLoopJob` | Forward rendering draw call group |
| `Camera.RenderSkybox` | Skybox rendering |
| `Camera.ImageEffects` | Post-processing |
| `Shadows.RenderShadowMap` | Shadow map generation |
