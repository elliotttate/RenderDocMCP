"""
RenderDoc API Facade
Provides thread-safe access to RenderDoc's ReplayController and CaptureContext.
Uses BlockInvoke to marshal calls to the replay thread.
"""

from .services import (
    CaptureManager,
    ActionService,
    SearchService,
    ResourceService,
    PipelineService,
    AnalysisService,
)


class RenderDocFacade:
    """
    Facade for RenderDoc API access.

    This class delegates all operations to specialized service classes:
    - CaptureManager: Capture management (status, list, open)
    - ActionService: Draw call / action operations
    - SearchService: Reverse lookup searches
    - ResourceService: Texture and buffer data
    - PipelineService: Pipeline state and shader info
    """

    def __init__(self, ctx):
        """
        Initialize facade with CaptureContext.

        Args:
            ctx: The pyrenderdoc CaptureContext from register()
        """
        self.ctx = ctx

        # Initialize service classes
        self._capture = CaptureManager(ctx, self._invoke)
        self._action = ActionService(ctx, self._invoke)
        self._search = SearchService(ctx, self._invoke)
        self._resource = ResourceService(ctx, self._invoke)
        self._pipeline = PipelineService(ctx, self._invoke)
        self._analysis = AnalysisService(ctx, self._invoke)

    def _invoke(self, callback):
        """Invoke callback on replay thread via BlockInvoke"""
        self.ctx.Replay().BlockInvoke(callback)

    # ==================== Capture Management ====================

    def get_capture_status(self):
        """Check if a capture is loaded and get API info"""
        return self._capture.get_capture_status()

    def list_captures(self, directory):
        """List all .rdc files in the specified directory"""
        return self._capture.list_captures(directory)

    def open_capture(self, capture_path):
        """Open a capture file in RenderDoc"""
        return self._capture.open_capture(capture_path)

    # ==================== Draw Call / Action Operations ====================

    def get_draw_calls(
        self,
        include_children=True,
        marker_filter=None,
        exclude_markers=None,
        event_id_min=None,
        event_id_max=None,
        only_actions=False,
        flags_filter=None,
    ):
        """Get all draw calls/actions in the capture with optional filtering"""
        return self._action.get_draw_calls(
            include_children=include_children,
            marker_filter=marker_filter,
            exclude_markers=exclude_markers,
            event_id_min=event_id_min,
            event_id_max=event_id_max,
            only_actions=only_actions,
            flags_filter=flags_filter,
        )

    def get_frame_summary(self):
        """Get a summary of the current capture frame"""
        return self._action.get_frame_summary()

    def get_draw_call_details(self, event_id):
        """Get detailed information about a specific draw call"""
        return self._action.get_draw_call_details(event_id)

    def get_action_timings(self, event_ids=None, marker_filter=None, exclude_markers=None, top_n=0):
        """Get GPU timing information for actions"""
        return self._action.get_action_timings(
            event_ids=event_ids,
            marker_filter=marker_filter,
            exclude_markers=exclude_markers,
            top_n=top_n,
        )

    # ==================== Search Operations ====================

    def find_draws_by_shader(self, shader_name, stage=None, max_results=0):
        """Find all draw calls using a shader with the given name (partial match)"""
        return self._search.find_draws_by_shader(shader_name, stage, max_results=max_results)

    def find_draws_by_texture(self, texture_name, max_results=0):
        """Find all draw calls using a texture with the given name (partial match)"""
        return self._search.find_draws_by_texture(texture_name, max_results=max_results)

    def find_draws_by_resource(self, resource_id, max_results=0):
        """Find all draw calls using a specific resource ID (exact match)"""
        return self._search.find_draws_by_resource(resource_id, max_results=max_results)

    # ==================== Resource Operations ====================

    def get_buffer_contents(self, resource_id, offset=0, length=0, event_id=None):
        """Get buffer data"""
        return self._resource.get_buffer_contents(resource_id, offset, length, event_id)

    def get_texture_info(self, resource_id):
        """Get texture metadata"""
        return self._resource.get_texture_info(resource_id)

    def get_texture_data(
        self,
        resource_id,
        mip=0,
        slice=0,
        sample=0,
        depth_slice=None,
        event_id=None,
    ):
        """Get texture pixel data"""
        return self._resource.get_texture_data(
            resource_id,
            mip,
            slice,
            sample,
            depth_slice,
            event_id,
        )

    # ==================== Pipeline Operations ====================

    def get_shader_info(self, event_id, stage):
        """Get shader information for a specific stage"""
        return self._pipeline.get_shader_info(event_id, stage)

    def get_pipeline_state(self, event_id):
        """Get full pipeline state at an event"""
        return self._pipeline.get_pipeline_state(event_id)

    # ==================== LLM-Focused High-Level Analysis ====================

    def get_event_insight(
        self,
        event_id,
        include_shader_disassembly=False,
        include_shader_constants=False,
        max_resources_per_stage=8,
        max_cbuffer_variables=24,
        disassembly_char_limit=24000,
    ):
        """Get compact, high-signal event snapshot for LLM workflows."""
        return self._analysis.get_event_insight(
            event_id,
            include_shader_disassembly=include_shader_disassembly,
            include_shader_constants=include_shader_constants,
            max_resources_per_stage=max_resources_per_stage,
            max_cbuffer_variables=max_cbuffer_variables,
            disassembly_char_limit=disassembly_char_limit,
        )

    def get_frame_digest(
        self,
        max_hotspots=12,
        max_markers=12,
        marker_filter=None,
        event_id_min=None,
        event_id_max=None,
        include_event_insights=False,
        event_insight_budget=3,
        max_resources_per_stage=8,
    ):
        """Get a compact frame-level digest optimized for LLM triage."""
        return self._analysis.get_frame_digest(
            max_hotspots=max_hotspots,
            max_markers=max_markers,
            marker_filter=marker_filter,
            event_id_min=event_id_min,
            event_id_max=event_id_max,
            include_event_insights=include_event_insights,
            event_insight_budget=event_insight_budget,
            max_resources_per_stage=max_resources_per_stage,
        )
