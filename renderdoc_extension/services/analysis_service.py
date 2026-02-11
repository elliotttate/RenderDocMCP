"""
LLM-oriented analysis service for RenderDoc.

Provides compact, high-signal snapshots that reduce MCP round-trips.
"""

import renderdoc as rd

from ..utils import Helpers, Serializers


class AnalysisService:
    """High-level analysis service for LLM workflows."""

    def __init__(self, ctx, invoke_fn):
        self.ctx = ctx
        self._invoke = invoke_fn

    def get_event_insight(
        self,
        event_id,
        include_shader_disassembly=False,
        include_shader_constants=False,
        max_resources_per_stage=8,
        max_cbuffer_variables=24,
        disassembly_char_limit=24000,
    ):
        """
        Build a compact, actionable snapshot for a specific event.

        The payload is designed for LLM agents: stable shape, bounded lists,
        and explicit heuristics/next steps.
        """
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("No capture loaded")

        result = {"data": None, "error": None}

        def callback(controller):
            controller.SetFrameEvent(event_id, True)

            action = self.ctx.GetAction(event_id)
            if not action:
                result["error"] = "No action at event %d" % event_id
                return

            structured_file = controller.GetStructuredFile()
            pipe = controller.GetPipelineState()
            tex_map, buf_map = self._build_resource_maps(controller)

            marker_path = self._find_marker_path(
                controller.GetRootActions(), structured_file, event_id
            )
            action_info = self._serialize_action(action, structured_file)
            output_info = self._collect_output_state(controller, pipe, tex_map, buf_map)
            ia_info = self._collect_input_assembly_state(controller, pipe)
            stage_info = self._collect_stage_state(
                controller,
                pipe,
                tex_map,
                buf_map,
                include_shader_disassembly=include_shader_disassembly,
                include_shader_constants=include_shader_constants,
                max_resources_per_stage=max_resources_per_stage,
                max_cbuffer_variables=max_cbuffer_variables,
                disassembly_char_limit=disassembly_char_limit,
            )

            heuristics = self._build_heuristics(action_info, stage_info, output_info)
            next_calls = self._recommend_next_calls(
                event_id,
                stage_info,
                output_info,
                include_shader_disassembly,
            )

            result["data"] = {
                "schema_version": "event_insight.v1",
                "event_id": event_id,
                "marker_path": marker_path,
                "action": action_info,
                "input_assembly": ia_info,
                "outputs": output_info,
                "stages": stage_info,
                "heuristics": heuristics,
                "recommended_next_calls": next_calls,
            }

        self._invoke(callback)

        if result["error"]:
            raise ValueError(result["error"])
        return result["data"]

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
        """
        Build a compact frame-level digest for LLM-first triage.

        The digest prioritizes:
        - hotspots by GPU duration (if available)
        - marker context around expensive regions
        - explicit anomalies and next-call guidance
        """
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("No capture loaded")

        max_hotspots = max(1, min(int(max_hotspots), 64))
        max_markers = max(1, min(int(max_markers), 64))
        event_insight_budget = max(0, min(int(event_insight_budget), 8))
        max_resources_per_stage = max(1, min(int(max_resources_per_stage), 24))

        result = {"data": None, "error": None}

        def callback(controller):
            root_actions = controller.GetRootActions()
            structured_file = controller.GetStructuredFile()
            flat_actions = Helpers.flatten_actions(root_actions)

            action_rows = []
            stats = {
                "draw_calls": 0,
                "dispatches": 0,
                "clears": 0,
                "copies": 0,
                "presents": 0,
                "markers": 0,
            }
            non_marker_count = 0

            for action, marker_path in self._iter_actions_with_marker_path(
                root_actions, structured_file
            ):
                flags = Serializers.serialize_flags(action.flags)
                flags_set = set(flags)
                is_marker = (
                    "PushMarker" in flags_set
                    or "SetMarker" in flags_set
                    or "PopMarker" in flags_set
                )
                if is_marker:
                    stats["markers"] += 1
                else:
                    non_marker_count += 1

                if "Drawcall" in flags_set:
                    stats["draw_calls"] += 1
                if "Dispatch" in flags_set:
                    stats["dispatches"] += 1
                if "Clear" in flags_set:
                    stats["clears"] += 1
                if "Copy" in flags_set:
                    stats["copies"] += 1
                if "Present" in flags_set:
                    stats["presents"] += 1

                if is_marker:
                    continue

                if event_id_min is not None and action.eventId < int(event_id_min):
                    continue
                if event_id_max is not None and action.eventId > int(event_id_max):
                    continue
                if marker_filter:
                    marker_text = "/".join(marker_path).lower()
                    if str(marker_filter).lower() not in marker_text:
                        continue

                action_rows.append(
                    {
                        "event_id": action.eventId,
                        "name": action.GetName(structured_file),
                        "flags": flags,
                        "marker_path": marker_path,
                        "num_indices": action.numIndices,
                        "num_instances": action.numInstances,
                    }
                )

            timings_payload = self._collect_frame_timings(controller, action_rows)
            hotspots = self._build_hotspots(
                action_rows,
                timings_payload["timing_map"],
                max_hotspots=max_hotspots,
            )
            marker_overview = self._build_marker_overview(
                root_actions,
                structured_file,
                timings_payload["timing_map"],
                max_markers=max_markers,
            )
            anomalies = self._build_frame_anomalies(
                stats,
                timings_payload["available"],
                hotspots,
            )
            recommended_calls = self._recommend_frame_next_calls(hotspots)

            digest = {
                "schema_version": "frame_digest.v1",
                "api": str(controller.GetAPIProperties().pipelineType),
                "total_actions": len(flat_actions),
                "non_marker_actions": non_marker_count,
                "statistics": stats,
                "timing": {
                    "available": timings_payload["available"],
                    "unit": timings_payload["unit"],
                    "sampled_event_count": len(timings_payload["timing_map"]),
                },
                "hotspots": hotspots,
                "marker_overview": marker_overview,
                "anomalies": anomalies,
                "recommended_next_calls": recommended_calls,
            }

            if include_event_insights and hotspots and event_insight_budget > 0:
                digest["hotspot_event_insights"] = self._collect_hotspot_insights(
                    controller,
                    root_actions,
                    structured_file,
                    hotspots[:event_insight_budget],
                    max_resources_per_stage=max_resources_per_stage,
                )

            result["data"] = digest

        self._invoke(callback)

        if result["error"]:
            raise ValueError(result["error"])
        return result["data"]

    def _serialize_action(self, action, structured_file):
        return {
            "event_id": action.eventId,
            "action_id": action.actionId,
            "name": action.GetName(structured_file),
            "flags": Serializers.serialize_flags(action.flags),
            "num_indices": action.numIndices,
            "num_instances": action.numInstances,
            "base_vertex": action.baseVertex,
            "vertex_offset": action.vertexOffset,
            "instance_offset": action.instanceOffset,
            "index_offset": action.indexOffset,
        }

    def _collect_stage_state(
        self,
        controller,
        pipe,
        tex_map,
        buf_map,
        include_shader_disassembly,
        include_shader_constants,
        max_resources_per_stage,
        max_cbuffer_variables,
        disassembly_char_limit,
    ):
        stages = {}

        for stage in Helpers.get_all_shader_stages():
            shader = pipe.GetShader(stage)
            if shader == rd.ResourceId.Null():
                continue

            stage_name = self._stage_name(stage)
            reflection = pipe.GetShaderReflection(stage)
            entry_point = pipe.GetShaderEntryPoint(stage)

            stage_payload = {
                "shader": {
                    "resource_id": str(shader),
                    "entry_point": entry_point,
                },
                "resource_counts": {
                    "srvs": 0,
                    "uavs": 0,
                    "samplers": 0,
                    "constant_buffers": 0,
                },
                "srvs_preview": [],
                "uavs_preview": [],
                "samplers_preview": [],
                "constant_buffers_preview": [],
                "truncated": {
                    "srvs": False,
                    "uavs": False,
                    "samplers": False,
                    "constant_buffers": False,
                },
            }

            shader_name = self._safe_resource_name(shader)
            if shader_name:
                stage_payload["shader"]["resource_name"] = shader_name

            # SRVs
            srv_payload = self._collect_read_only_resources(
                pipe, stage, tex_map, buf_map, max_resources_per_stage
            )
            stage_payload["resource_counts"]["srvs"] = srv_payload["count"]
            stage_payload["srvs_preview"] = srv_payload["preview"]
            stage_payload["truncated"]["srvs"] = srv_payload["truncated"]

            # UAVs
            uav_payload = self._collect_read_write_resources(
                pipe, stage, tex_map, buf_map, max_resources_per_stage
            )
            stage_payload["resource_counts"]["uavs"] = uav_payload["count"]
            stage_payload["uavs_preview"] = uav_payload["preview"]
            stage_payload["truncated"]["uavs"] = uav_payload["truncated"]

            # Samplers
            sampler_payload = self._collect_samplers(
                pipe, stage, max_resources_per_stage
            )
            stage_payload["resource_counts"]["samplers"] = sampler_payload["count"]
            stage_payload["samplers_preview"] = sampler_payload["preview"]
            stage_payload["truncated"]["samplers"] = sampler_payload["truncated"]

            # Constant buffers
            cbuffer_payload = self._collect_constant_buffers(
                controller,
                pipe,
                stage,
                reflection,
                include_shader_constants,
                max_resources_per_stage,
                max_cbuffer_variables,
            )
            stage_payload["resource_counts"]["constant_buffers"] = cbuffer_payload["count"]
            stage_payload["constant_buffers_preview"] = cbuffer_payload["preview"]
            stage_payload["truncated"]["constant_buffers"] = cbuffer_payload["truncated"]

            if include_shader_disassembly and reflection:
                stage_payload["disassembly"] = self._get_disassembly(
                    controller, pipe, reflection, disassembly_char_limit
                )

            stages[stage_name] = stage_payload

        return stages

    def _collect_read_only_resources(
        self, pipe, stage, tex_map, buf_map, max_resources_per_stage
    ):
        preview = []
        count = 0
        truncated = False
        get_constant_buffer = getattr(pipe, "GetConstantBuffer", None)
        can_read_values = callable(get_constant_buffer)

        try:
            srvs = pipe.GetReadOnlyResources(stage, False)
            for srv in srvs:
                desc = getattr(srv, "descriptor", None)
                if not desc:
                    continue
                res_id = getattr(desc, "resource", rd.ResourceId.Null())
                if res_id == rd.ResourceId.Null():
                    continue

                count += 1
                if len(preview) >= max_resources_per_stage:
                    truncated = True
                    continue

                slot = getattr(getattr(srv, "access", None), "index", len(preview))
                item = {
                    "slot": slot,
                    "resource_id": str(res_id),
                    "first_mip": getattr(desc, "firstMip", 0),
                    "num_mips": getattr(desc, "numMips", 1),
                    "first_slice": getattr(desc, "firstSlice", 0),
                    "num_slices": getattr(desc, "numSlices", 1),
                }
                self._append_resource_identity(item, res_id, tex_map, buf_map)
                preview.append(item)
        except Exception as e:
            preview.append({"error": str(e)})

        return {"count": count, "preview": preview, "truncated": truncated}

    def _collect_read_write_resources(
        self, pipe, stage, tex_map, buf_map, max_resources_per_stage
    ):
        preview = []
        count = 0
        truncated = False

        try:
            uavs = pipe.GetReadWriteResources(stage, False)
            for uav in uavs:
                desc = getattr(uav, "descriptor", None)
                if not desc:
                    continue
                res_id = getattr(desc, "resource", rd.ResourceId.Null())
                if res_id == rd.ResourceId.Null():
                    continue

                count += 1
                if len(preview) >= max_resources_per_stage:
                    truncated = True
                    continue

                slot = getattr(getattr(uav, "access", None), "index", len(preview))
                item = {
                    "slot": slot,
                    "resource_id": str(res_id),
                    "first_element": getattr(desc, "firstMip", 0),
                    "num_elements": getattr(desc, "numMips", 0),
                }
                self._append_resource_identity(item, res_id, tex_map, buf_map)
                preview.append(item)
        except Exception as e:
            preview.append({"error": str(e)})

        return {"count": count, "preview": preview, "truncated": truncated}

    def _collect_samplers(self, pipe, stage, max_resources_per_stage):
        preview = []
        count = 0
        truncated = False

        try:
            samplers = pipe.GetSamplers(stage, False)
            for sampler in samplers:
                count += 1
                if len(preview) >= max_resources_per_stage:
                    truncated = True
                    continue

                slot = getattr(getattr(sampler, "access", None), "index", len(preview))
                desc = getattr(sampler, "descriptor", None)
                item = {"slot": slot}
                if desc:
                    for attr, key in [
                        ("addressU", "address_u"),
                        ("addressV", "address_v"),
                        ("addressW", "address_w"),
                        ("filter", "filter"),
                        ("maxAnisotropy", "max_anisotropy"),
                        ("minLOD", "min_lod"),
                        ("maxLOD", "max_lod"),
                        ("mipLODBias", "mip_lod_bias"),
                    ]:
                        val = getattr(desc, attr, None)
                        if val is not None:
                            item[key] = str(val)
                preview.append(item)
        except Exception as e:
            preview.append({"error": str(e)})

        return {"count": count, "preview": preview, "truncated": truncated}

    def _collect_constant_buffers(
        self,
        controller,
        pipe,
        stage,
        reflection,
        include_shader_constants,
        max_resources_per_stage,
        max_cbuffer_variables,
    ):
        preview = []
        count = 0
        truncated = False

        if not reflection:
            return {"count": 0, "preview": preview, "truncated": False}

        for cb_index, cb in enumerate(reflection.constantBlocks):
            count += 1
            if len(preview) >= max_resources_per_stage:
                truncated = True
                continue

            slot = getattr(cb, "bindPoint", getattr(cb, "fixedBindNumber", cb_index))
            entry = {
                "slot": slot,
                "name": cb.name,
                "byte_size": cb.byteSize,
                "variable_count": len(cb.variables) if cb.variables else 0,
                "variables_preview": [],
                "variables_truncated": False,
            }

            if cb.variables:
                var_names = []
                for var in cb.variables:
                    if len(var_names) >= max_cbuffer_variables:
                        entry["variables_truncated"] = True
                        break
                    var_names.append(var.name)
                entry["variables_preview"] = var_names

            if include_shader_constants and can_read_values:
                try:
                    bind = get_constant_buffer(stage, cb_index, 0)
                    if bind.resourceId != rd.ResourceId.Null():
                        raw_vars = controller.GetCBufferVariableContents(
                            pipe.GetGraphicsPipelineObject(),
                            reflection.resourceId,
                            stage,
                            reflection.entryPoint,
                            cb_index,
                            bind.resourceId,
                            bind.byteOffset,
                            bind.byteSize,
                        )
                        serialized = Serializers.serialize_variables(raw_vars)
                        if len(serialized) > max_cbuffer_variables:
                            entry["variables_values"] = serialized[:max_cbuffer_variables]
                            entry["variables_truncated"] = True
                        else:
                            entry["variables_values"] = serialized
                except Exception as e:
                    entry["values_error"] = str(e)
            elif include_shader_constants and not can_read_values:
                entry["values_unavailable"] = (
                    "PipeState.GetConstantBuffer is unavailable in this RenderDoc build."
                )

            preview.append(entry)

        return {"count": count, "preview": preview, "truncated": truncated}

    def _get_disassembly(self, controller, pipe, reflection, disassembly_char_limit):
        payload = {"text": "", "truncated": False}
        try:
            targets = controller.GetDisassemblyTargets(True)
            if not targets:
                return payload
            disasm = controller.DisassembleShader(
                pipe.GetGraphicsPipelineObject(), reflection, targets[0]
            )
            if disassembly_char_limit > 0 and len(disasm) > disassembly_char_limit:
                payload["text"] = disasm[:disassembly_char_limit]
                payload["truncated"] = True
            else:
                payload["text"] = disasm
        except Exception as e:
            payload["error"] = str(e)
        return payload

    def _collect_output_state(self, controller, pipe, tex_map, buf_map):
        info = {
            "render_targets": [],
            "depth_target": None,
            "render_target_count": 0,
            "has_depth_target": False,
        }

        try:
            om = None
            get_output_merger = getattr(pipe, "GetOutputMerger", None)
            if callable(get_output_merger):
                om = get_output_merger()

            if om:
                for i, rt in enumerate(getattr(om, "renderTargets", [])):
                    res_id = self._extract_res_id(rt)
                    if res_id == rd.ResourceId.Null():
                        continue
                    item = {"index": i, "resource_id": str(res_id)}
                    self._append_resource_identity(item, res_id, tex_map, buf_map)
                    info["render_targets"].append(item)

                depth_id = self._extract_res_id(getattr(om, "depthTarget", None))
                if depth_id != rd.ResourceId.Null():
                    item = {"resource_id": str(depth_id)}
                    self._append_resource_identity(item, depth_id, tex_map, buf_map)
                    info["depth_target"] = item
                    info["has_depth_target"] = True
            else:
                # Fallback for builds where generic PipeState output merger APIs
                # are not exposed.
                d3d11_state = controller.GetD3D11PipelineState()
                om11 = getattr(d3d11_state, "outputMerger", None) if d3d11_state else None
                if om11:
                    for i, rt in enumerate(getattr(om11, "renderTargets", [])):
                        res_id = self._extract_res_id(rt)
                        if res_id == rd.ResourceId.Null():
                            continue
                        item = {"index": i, "resource_id": str(res_id)}
                        self._append_resource_identity(item, res_id, tex_map, buf_map)
                        info["render_targets"].append(item)

                    depth_id = self._extract_res_id(getattr(om11, "depthTarget", None))
                    if depth_id != rd.ResourceId.Null():
                        item = {"resource_id": str(depth_id)}
                        self._append_resource_identity(item, depth_id, tex_map, buf_map)
                        info["depth_target"] = item
                        info["has_depth_target"] = True
        except Exception as e:
            info["error"] = str(e)

        info["render_target_count"] = len(info["render_targets"])
        return info

    def _collect_input_assembly_state(self, controller, pipe):
        info = {}

        try:
            ia = pipe.GetIAState()
            if ia:
                info["topology"] = str(ia.topology)
        except Exception:
            pass

        try:
            d3d11_state = controller.GetD3D11PipelineState()
            if not d3d11_state:
                return info
            ia11 = d3d11_state.inputAssembly
            if not ia11:
                return info

            vb_count = 0
            vb_preview = []
            for slot, vb in enumerate(getattr(ia11, "vertexBuffers", [])):
                desc = getattr(vb, "descriptor", vb)
                res = getattr(
                    desc,
                    "resourceId",
                    getattr(desc, "resource", rd.ResourceId.Null()),
                )
                if res == rd.ResourceId.Null():
                    continue
                vb_count += 1
                if len(vb_preview) < 8:
                    vb_preview.append(
                        {
                            "slot": slot,
                            "resource_id": str(res),
                            "stride": getattr(desc, "byteStride", getattr(desc, "stride", None)),
                            "offset": getattr(desc, "byteOffset", getattr(desc, "offset", None)),
                        }
                    )
            info["vertex_buffer_count"] = vb_count
            info["vertex_buffers_preview"] = vb_preview
            info["vertex_buffers_truncated"] = vb_count > len(vb_preview)

            idx = getattr(ia11, "indexBuffer", None)
            if idx is not None:
                idx_desc = getattr(idx, "descriptor", idx)
                idx_res = getattr(
                    idx_desc,
                    "resourceId",
                    getattr(idx_desc, "resource", rd.ResourceId.Null()),
                )
                if idx_res != rd.ResourceId.Null():
                    info["index_buffer"] = {
                        "resource_id": str(idx_res),
                        "offset": getattr(idx_desc, "byteOffset", getattr(idx_desc, "offset", 0)),
                        "format": str(getattr(idx_desc, "format", "")),
                    }
        except Exception:
            pass

        return info

    def _build_heuristics(self, action_info, stage_info, output_info):
        heuristics = []
        flags = set(action_info.get("flags", []))

        if "Drawcall" in flags:
            if "vertex" not in stage_info:
                heuristics.append(
                    {
                        "severity": "high",
                        "code": "missing_vertex_shader",
                        "message": "Drawcall has no vertex shader bound.",
                    }
                )
            if "pixel" not in stage_info:
                heuristics.append(
                    {
                        "severity": "medium",
                        "code": "missing_pixel_shader",
                        "message": "Drawcall has no pixel shader bound.",
                    }
                )
            if (
                output_info.get("render_target_count", 0) == 0
                and not output_info.get("has_depth_target", False)
                and not output_info.get("error")
            ):
                heuristics.append(
                    {
                        "severity": "high",
                        "code": "no_outputs_bound",
                        "message": "No render targets or depth target bound for drawcall.",
                    }
                )

            if (
                action_info.get("num_indices", 0) == 0
                and action_info.get("num_instances", 0) <= 1
                and "Dispatch" not in flags
            ):
                heuristics.append(
                    {
                        "severity": "low",
                        "code": "small_or_empty_draw",
                        "message": "Draw has zero indices and <=1 instance; verify this is expected.",
                    }
                )

        for stage_name, stage_payload in stage_info.items():
            trunc = stage_payload.get("truncated", {})
            truncated_fields = [k for k, v in trunc.items() if v]
            if truncated_fields:
                heuristics.append(
                    {
                        "severity": "info",
                        "code": "stage_preview_truncated",
                        "message": "Preview truncated for stage '%s': %s"
                        % (stage_name, ", ".join(truncated_fields)),
                    }
                )

        return heuristics

    def _build_frame_anomalies(self, stats, timings_available, hotspots):
        anomalies = []

        if stats.get("draw_calls", 0) == 0 and stats.get("dispatches", 0) == 0:
            anomalies.append(
                {
                    "severity": "high",
                    "code": "no_draw_or_dispatch",
                    "message": "No drawcalls or dispatches in filtered frame scope.",
                }
            )

        if not timings_available:
            anomalies.append(
                {
                    "severity": "info",
                    "code": "timing_unavailable",
                    "message": "GPU timing counters are unavailable for this capture.",
                }
            )

        if hotspots:
            top = hotspots[0]
            if top.get("duration_ms", 0.0) >= 8.0:
                anomalies.append(
                    {
                        "severity": "medium",
                        "code": "single_hotspot_dominates",
                        "message": "Top hotspot is %.3f ms at event %d."
                        % (top.get("duration_ms", 0.0), top.get("event_id", 0)),
                    }
                )
            for hotspot in hotspots:
                flags = set(hotspot.get("flags", []))
                if (
                    hotspot.get("duration_ms", 0.0) > 0.5
                    and "Drawcall" in flags
                    and hotspot.get("num_indices", 0) == 0
                ):
                    anomalies.append(
                        {
                            "severity": "low",
                            "code": "expensive_zero_index_draw",
                            "message": "Event %d took %.3f ms with zero indices."
                            % (
                                hotspot.get("event_id", 0),
                                hotspot.get("duration_ms", 0.0),
                            ),
                        }
                    )
                    break

        return anomalies

    def _recommend_next_calls(
        self, event_id, stage_info, output_info, include_shader_disassembly
    ):
        calls = ["get_pipeline_state(event_id=%d)" % event_id]

        if not include_shader_disassembly:
            for stage in ["vertex", "pixel", "compute"]:
                if stage in stage_info:
                    calls.append(
                        "get_shader_info(event_id=%d, stage='%s')" % (event_id, stage)
                    )

        for rt in output_info.get("render_targets", [])[:2]:
            calls.append("get_texture_info(resource_id='%s')" % rt["resource_id"])
        depth = output_info.get("depth_target")
        if depth and depth.get("resource_id"):
            calls.append("get_texture_info(resource_id='%s')" % depth["resource_id"])

        return calls

    @staticmethod
    def _recommend_frame_next_calls(hotspots):
        calls = ["get_frame_summary()"]
        for hotspot in hotspots[:5]:
            event_id = hotspot.get("event_id")
            if event_id is None:
                continue
            calls.append("get_event_insight(event_id=%d)" % event_id)
        return calls

    def _collect_frame_timings(self, controller, action_rows):
        try:
            counters = controller.EnumerateCounters()
            if rd.GPUCounter.EventGPUDuration not in counters:
                return {"available": False, "unit": None, "timing_map": {}}

            counter_desc = controller.DescribeCounter(rd.GPUCounter.EventGPUDuration)
            counter_results = controller.FetchCounters([rd.GPUCounter.EventGPUDuration])
            target_counter = int(rd.GPUCounter.EventGPUDuration)
            relevant_ids = {row["event_id"] for row in action_rows}
            timing_map = {}
            for row in counter_results:
                if row.counter != target_counter:
                    continue
                if row.eventId not in relevant_ids:
                    continue
                timing_map[row.eventId] = float(row.value.d) * 1000.0
            return {"available": True, "unit": str(counter_desc.unit), "timing_map": timing_map}
        except Exception:
            return {"available": False, "unit": None, "timing_map": {}}

    @staticmethod
    def _build_hotspots(action_rows, timing_map, max_hotspots):
        hotspots = []
        for row in action_rows:
            event_id = row["event_id"]
            duration_ms = float(timing_map.get(event_id, 0.0))
            hotspots.append(
                {
                    "event_id": event_id,
                    "name": row["name"],
                    "flags": row["flags"],
                    "marker_path": row["marker_path"],
                    "duration_ms": round(duration_ms, 6),
                    "num_indices": row["num_indices"],
                    "num_instances": row["num_instances"],
                }
            )

        hotspots.sort(key=lambda item: item["duration_ms"], reverse=True)
        limited = hotspots[:max_hotspots]
        for idx, item in enumerate(limited, start=1):
            item["rank"] = idx
        return limited

    def _build_marker_overview(
        self, root_actions, structured_file, timing_map, max_markers
    ):
        markers = []
        for action in root_actions:
            flags = action.flags
            if not (
                (flags & rd.ActionFlags.PushMarker) or (flags & rd.ActionFlags.SetMarker)
            ):
                continue

            scan = self._scan_marker_subtree(action, timing_map)
            marker = {
                "name": action.GetName(structured_file),
                "event_id": action.eventId,
                "first_event_id": scan["first_event_id"],
                "last_event_id": scan["last_event_id"],
                "child_count": Helpers.count_children(action),
                "draw_calls": scan["draw_calls"],
                "dispatches": scan["dispatches"],
                "gpu_time_ms": round(scan["gpu_time_ms"], 6),
            }
            markers.append(marker)

        markers.sort(
            key=lambda item: (
                item.get("gpu_time_ms", 0.0),
                item.get("draw_calls", 0),
                item.get("dispatches", 0),
            ),
            reverse=True,
        )
        return markers[:max_markers]

    def _scan_marker_subtree(self, action, timing_map):
        summary = {
            "first_event_id": action.eventId,
            "last_event_id": action.eventId,
            "draw_calls": 0,
            "dispatches": 0,
            "gpu_time_ms": 0.0,
        }

        def _visit(node):
            flags = Serializers.serialize_flags(node.flags)
            flags_set = set(flags)

            if node.eventId < summary["first_event_id"]:
                summary["first_event_id"] = node.eventId
            if node.eventId > summary["last_event_id"]:
                summary["last_event_id"] = node.eventId

            if "Drawcall" in flags_set:
                summary["draw_calls"] += 1
            if "Dispatch" in flags_set:
                summary["dispatches"] += 1

            summary["gpu_time_ms"] += float(timing_map.get(node.eventId, 0.0))

            for child in node.children or []:
                _visit(child)

        _visit(action)
        return summary

    def _collect_hotspot_insights(
        self,
        controller,
        root_actions,
        structured_file,
        hotspots,
        max_resources_per_stage=8,
    ):
        tex_map, buf_map = self._build_resource_maps(controller)
        insights = []
        for hotspot in hotspots:
            event_id = hotspot.get("event_id")
            if event_id is None:
                continue
            try:
                controller.SetFrameEvent(event_id, True)
                action = self.ctx.GetAction(event_id)
                if not action:
                    continue
                pipe = controller.GetPipelineState()
                stage_info = self._collect_stage_state(
                    controller,
                    pipe,
                    tex_map,
                    buf_map,
                    include_shader_disassembly=False,
                    include_shader_constants=False,
                    max_resources_per_stage=max_resources_per_stage,
                    max_cbuffer_variables=12,
                    disassembly_char_limit=0,
                )
                output_info = self._collect_output_state(controller, pipe, tex_map, buf_map)
                action_info = self._serialize_action(action, structured_file)
                heuristics = self._build_heuristics(action_info, stage_info, output_info)
                insights.append(
                    {
                        "event_id": event_id,
                        "marker_path": self._find_marker_path(
                            root_actions, structured_file, event_id
                        ),
                        "stages_present": sorted(stage_info.keys()),
                        "render_target_count": output_info.get("render_target_count", 0),
                        "has_depth_target": output_info.get("has_depth_target", False),
                        "heuristics": heuristics,
                    }
                )
            except Exception as e:
                insights.append({"event_id": event_id, "error": str(e)})

        return insights

    def _append_resource_identity(self, item, resource_id, tex_map, buf_map):
        name = self._safe_resource_name(resource_id)
        if name:
            item["resource_name"] = name

        key = str(resource_id)
        tex = tex_map.get(key)
        if tex is not None:
            item["type"] = "texture"
            item["width"] = tex.width
            item["height"] = tex.height
            item["depth"] = tex.depth
            item["array_size"] = tex.arraysize
            item["mip_levels"] = tex.mips
            item["format"] = str(tex.format.Name())
            item["dimension"] = str(tex.type)
            return

        buf = buf_map.get(key)
        if buf is not None:
            item["type"] = "buffer"
            item["length"] = buf.length

    def _safe_resource_name(self, resource_id):
        try:
            name = self.ctx.GetResourceName(resource_id)
            if not name:
                return ""
            return name.encode("ascii", "replace").decode("ascii")
        except Exception:
            return ""

    def _find_marker_path(self, actions, structured_file, target_event_id):
        def _visit(current_actions, stack):
            for action in current_actions:
                flags = action.flags
                is_push = bool(flags & rd.ActionFlags.PushMarker)
                is_set = bool(flags & rd.ActionFlags.SetMarker)

                next_stack = stack
                if is_push or is_set:
                    next_stack = stack + [action.GetName(structured_file)]

                if action.eventId == target_event_id:
                    return next_stack

                if action.children:
                    found = _visit(action.children, next_stack)
                    if found is not None:
                        return found
            return None

        found = _visit(actions, [])
        return found or []

    def _iter_actions_with_marker_path(self, actions, structured_file, stack=None):
        if stack is None:
            stack = []
        for action in actions:
            flags = action.flags
            is_push = bool(flags & rd.ActionFlags.PushMarker)
            is_set = bool(flags & rd.ActionFlags.SetMarker)
            is_pop = bool(flags & rd.ActionFlags.PopMarker)

            current_stack = stack
            if is_push or is_set:
                current_stack = stack + [action.GetName(structured_file)]
            elif is_pop:
                current_stack = stack[:-1] if stack else stack

            yield action, current_stack

            if action.children:
                for child, child_stack in self._iter_actions_with_marker_path(
                    action.children, structured_file, current_stack
                ):
                    yield child, child_stack

    @staticmethod
    def _extract_res_id(obj):
        if obj is None:
            return rd.ResourceId.Null()
        return getattr(obj, "resourceId", getattr(obj, "resource", rd.ResourceId.Null()))

    @staticmethod
    def _build_resource_maps(controller):
        tex_map = {}
        for tex in controller.GetTextures():
            tex_map[str(tex.resourceId)] = tex

        buf_map = {}
        for buf in controller.GetBuffers():
            buf_map[str(buf.resourceId)] = buf

        return tex_map, buf_map

    @staticmethod
    def _stage_name(stage):
        mapping = {
            rd.ShaderStage.Vertex: "vertex",
            rd.ShaderStage.Hull: "hull",
            rd.ShaderStage.Domain: "domain",
            rd.ShaderStage.Geometry: "geometry",
            rd.ShaderStage.Pixel: "pixel",
            rd.ShaderStage.Compute: "compute",
        }
        return mapping.get(stage, str(stage).lower())
