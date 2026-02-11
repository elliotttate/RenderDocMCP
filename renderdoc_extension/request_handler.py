"""
Request Handler for RenderDoc MCP Bridge
Routes incoming requests to appropriate facade methods.
"""

import traceback


class RequestHandler:
    """Handles incoming MCP bridge requests"""

    def __init__(self, facade):
        self.facade = facade
        self._bridge_server = None
        self._methods = {
            "ping": self._handle_ping,
            "get_bridge_diagnostics": self._handle_get_bridge_diagnostics,
            "get_capture_status": self._handle_get_capture_status,
            "get_draw_calls": self._handle_get_draw_calls,
            "get_frame_summary": self._handle_get_frame_summary,
            "find_draws_by_shader": self._handle_find_draws_by_shader,
            "find_draws_by_texture": self._handle_find_draws_by_texture,
            "find_draws_by_resource": self._handle_find_draws_by_resource,
            "get_draw_call_details": self._handle_get_draw_call_details,
            "get_action_timings": self._handle_get_action_timings,
            "get_shader_info": self._handle_get_shader_info,
            "get_buffer_contents": self._handle_get_buffer_contents,
            "get_texture_info": self._handle_get_texture_info,
            "get_texture_data": self._handle_get_texture_data,
            "get_pipeline_state": self._handle_get_pipeline_state,
            "get_event_insight": self._handle_get_event_insight,
            "get_frame_digest": self._handle_get_frame_digest,
            "list_captures": self._handle_list_captures,
            "open_capture": self._handle_open_capture,
        }

    def set_bridge_server(self, server):
        """Attach bridge server instance to expose runtime diagnostics."""
        self._bridge_server = server

    def handle(self, request):
        """Handle a request and return response"""
        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params", {})

        try:
            if method not in self._methods:
                return self._error_response(
                    request_id, -32601, "Method not found: %s" % method
                )

            result = self._methods[method](params)
            return {"id": request_id, "result": result}

        except ValueError as e:
            return self._error_response(request_id, -32602, str(e))
        except Exception as e:
            traceback.print_exc()
            return self._error_response(request_id, -32000, str(e))

    def _error_response(self, request_id, code, message):
        """Create an error response"""
        return {"id": request_id, "error": {"code": code, "message": message}}

    def _handle_ping(self, params):
        """Handle ping request"""
        return {"status": "ok", "message": "pong"}

    def _handle_get_bridge_diagnostics(self, params):
        """Handle get_bridge_diagnostics request"""
        include_recent_errors = bool(params.get("include_recent_errors", True))
        max_recent_errors = int(params.get("max_recent_errors", 16))
        if self._bridge_server is None:
            return {
                "schema_version": "bridge_diagnostics.v1",
                "running": False,
                "error": "bridge server handle not attached",
            }
        return self._bridge_server.get_diagnostics(
            include_recent_errors=include_recent_errors,
            max_recent_errors=max_recent_errors,
        )

    def _handle_get_capture_status(self, params):
        """Handle get_capture_status request"""
        return self.facade.get_capture_status()

    def _handle_get_draw_calls(self, params):
        """Handle get_draw_calls request"""
        include_children = params.get("include_children", True)
        marker_filter = params.get("marker_filter")
        exclude_markers = params.get("exclude_markers")
        event_id_min = params.get("event_id_min")
        event_id_max = params.get("event_id_max")
        only_actions = params.get("only_actions", False)
        flags_filter = params.get("flags_filter")
        return self.facade.get_draw_calls(
            include_children=include_children,
            marker_filter=marker_filter,
            exclude_markers=exclude_markers,
            event_id_min=event_id_min,
            event_id_max=event_id_max,
            only_actions=only_actions,
            flags_filter=flags_filter,
        )

    def _handle_get_frame_summary(self, params):
        """Handle get_frame_summary request"""
        return self.facade.get_frame_summary()

    def _handle_find_draws_by_shader(self, params):
        """Handle find_draws_by_shader request"""
        shader_name = params.get("shader_name")
        if shader_name is None:
            raise ValueError("shader_name is required")
        stage = params.get("stage")
        max_results = params.get("max_results", 0)
        return self.facade.find_draws_by_shader(shader_name, stage, max_results=max_results)

    def _handle_find_draws_by_texture(self, params):
        """Handle find_draws_by_texture request"""
        texture_name = params.get("texture_name")
        if texture_name is None:
            raise ValueError("texture_name is required")
        max_results = params.get("max_results", 0)
        return self.facade.find_draws_by_texture(texture_name, max_results=max_results)

    def _handle_find_draws_by_resource(self, params):
        """Handle find_draws_by_resource request"""
        resource_id = params.get("resource_id")
        if resource_id is None:
            raise ValueError("resource_id is required")
        max_results = params.get("max_results", 0)
        return self.facade.find_draws_by_resource(resource_id, max_results=max_results)

    def _handle_get_draw_call_details(self, params):
        """Handle get_draw_call_details request"""
        event_id = params.get("event_id")
        if event_id is None:
            raise ValueError("event_id is required")
        return self.facade.get_draw_call_details(int(event_id))

    def _handle_get_action_timings(self, params):
        """Handle get_action_timings request"""
        event_ids = params.get("event_ids")
        marker_filter = params.get("marker_filter")
        exclude_markers = params.get("exclude_markers")
        top_n = params.get("top_n", 0)
        return self.facade.get_action_timings(
            event_ids=event_ids,
            marker_filter=marker_filter,
            exclude_markers=exclude_markers,
            top_n=top_n,
        )

    def _handle_get_shader_info(self, params):
        """Handle get_shader_info request"""
        event_id = params.get("event_id")
        stage = params.get("stage")
        if event_id is None:
            raise ValueError("event_id is required")
        if stage is None:
            raise ValueError("stage is required")
        return self.facade.get_shader_info(int(event_id), stage)

    def _handle_get_buffer_contents(self, params):
        """Handle get_buffer_contents request"""
        resource_id = params.get("resource_id")
        if resource_id is None:
            raise ValueError("resource_id is required")
        offset = params.get("offset", 0)
        length = params.get("length", 0)
        event_id = params.get("event_id")
        return self.facade.get_buffer_contents(resource_id, offset, length, event_id)

    def _handle_get_texture_info(self, params):
        """Handle get_texture_info request"""
        resource_id = params.get("resource_id")
        if resource_id is None:
            raise ValueError("resource_id is required")
        return self.facade.get_texture_info(resource_id)

    def _handle_get_texture_data(self, params):
        """Handle get_texture_data request"""
        resource_id = params.get("resource_id")
        if resource_id is None:
            raise ValueError("resource_id is required")
        mip = params.get("mip", 0)
        slice_idx = params.get("slice", 0)
        sample = params.get("sample", 0)
        depth_slice = params.get("depth_slice")  # None = full volume
        event_id = params.get("event_id")
        return self.facade.get_texture_data(
            resource_id,
            mip,
            slice_idx,
            sample,
            depth_slice,
            event_id,
        )

    def _handle_get_pipeline_state(self, params):
        """Handle get_pipeline_state request"""
        event_id = params.get("event_id")
        if event_id is None:
            raise ValueError("event_id is required")
        return self.facade.get_pipeline_state(int(event_id))

    def _handle_get_event_insight(self, params):
        """Handle get_event_insight request"""
        event_id = params.get("event_id")
        if event_id is None:
            raise ValueError("event_id is required")
        include_shader_disassembly = params.get("include_shader_disassembly", False)
        include_shader_constants = params.get("include_shader_constants", False)
        max_resources_per_stage = int(params.get("max_resources_per_stage", 8))
        max_cbuffer_variables = int(params.get("max_cbuffer_variables", 24))
        disassembly_char_limit = int(params.get("disassembly_char_limit", 24000))
        return self.facade.get_event_insight(
            int(event_id),
            include_shader_disassembly=include_shader_disassembly,
            include_shader_constants=include_shader_constants,
            max_resources_per_stage=max_resources_per_stage,
            max_cbuffer_variables=max_cbuffer_variables,
            disassembly_char_limit=disassembly_char_limit,
        )

    def _handle_get_frame_digest(self, params):
        """Handle get_frame_digest request"""
        max_hotspots = int(params.get("max_hotspots", 12))
        max_markers = int(params.get("max_markers", 12))
        marker_filter = params.get("marker_filter")
        event_id_min = params.get("event_id_min")
        event_id_max = params.get("event_id_max")
        include_event_insights = bool(params.get("include_event_insights", False))
        event_insight_budget = int(params.get("event_insight_budget", 3))
        max_resources_per_stage = int(params.get("max_resources_per_stage", 8))
        return self.facade.get_frame_digest(
            max_hotspots=max_hotspots,
            max_markers=max_markers,
            marker_filter=marker_filter,
            event_id_min=event_id_min,
            event_id_max=event_id_max,
            include_event_insights=include_event_insights,
            event_insight_budget=event_insight_budget,
            max_resources_per_stage=max_resources_per_stage,
        )

    def _handle_list_captures(self, params):
        """Handle list_captures request"""
        directory = params.get("directory")
        if directory is None:
            raise ValueError("directory is required")
        return self.facade.list_captures(directory)

    def _handle_open_capture(self, params):
        """Handle open_capture request"""
        capture_path = params.get("capture_path")
        if capture_path is None:
            raise ValueError("capture_path is required")
        return self.facade.open_capture(capture_path)
