"""
Pipeline state service for RenderDoc.
"""

import renderdoc as rd

from ..utils import Parsers, Serializers, Helpers


class PipelineService:
    """Pipeline state service"""

    def __init__(self, ctx, invoke_fn):
        self.ctx = ctx
        self._invoke = invoke_fn

    def get_shader_info(self, event_id, stage):
        """Get shader information for a specific stage"""
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("No capture loaded")

        result = {"shader": None, "error": None}

        def callback(controller):
            controller.SetFrameEvent(event_id, True)

            pipe = controller.GetPipelineState()
            stage_enum = Parsers.parse_stage(stage)

            shader = pipe.GetShader(stage_enum)
            if shader == rd.ResourceId.Null():
                result["error"] = "No %s shader bound" % stage
                return

            entry = pipe.GetShaderEntryPoint(stage_enum)
            reflection = pipe.GetShaderReflection(stage_enum)

            shader_info = {
                "resource_id": str(shader),
                "entry_point": entry,
                "stage": stage,
            }

            # Get disassembly
            try:
                targets = controller.GetDisassemblyTargets(True)
                if targets:
                    disasm = controller.DisassembleShader(
                        pipe.GetGraphicsPipelineObject(), reflection, targets[0]
                    )
                    shader_info["disassembly"] = disasm
            except Exception as e:
                shader_info["disassembly_error"] = str(e)

            # Get constant buffer info
            if reflection:
                shader_info["constant_buffers"] = self._get_cbuffer_info(
                    controller, pipe, reflection, stage_enum
                )
                shader_info["resources"] = self._get_resource_bindings(reflection)

            result["shader"] = shader_info

        self._invoke(callback)

        if result["error"]:
            raise ValueError(result["error"])
        return result["shader"]

    def get_pipeline_state(self, event_id):
        """Get full pipeline state at an event"""
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("No capture loaded")

        result = {"pipeline": None, "error": None}

        def callback(controller):
            controller.SetFrameEvent(event_id, True)

            pipe = controller.GetPipelineState()
            api = controller.GetAPIProperties().pipelineType

            # Build lookup maps once for O(1) resource lookups
            tex_map, buf_map = self._build_resource_maps(controller)

            pipeline_info = {
                "event_id": event_id,
                "api": str(api),
            }

            # Shader stages with detailed bindings
            stages = {}
            stage_list = Helpers.get_all_shader_stages()
            for stage in stage_list:
                shader = pipe.GetShader(stage)
                if shader != rd.ResourceId.Null():
                    stage_info = {
                        "resource_id": str(shader),
                        "entry_point": pipe.GetShaderEntryPoint(stage),
                    }

                    reflection = pipe.GetShaderReflection(stage)

                    stage_info["resources"] = self._get_stage_resources(
                        pipe, stage, reflection, tex_map, buf_map
                    )
                    stage_info["uavs"] = self._get_stage_uavs(
                        pipe, stage, reflection, tex_map, buf_map
                    )
                    stage_info["samplers"] = self._get_stage_samplers(
                        pipe, stage, reflection
                    )
                    stage_info["constant_buffers"] = self._get_stage_cbuffers(
                        controller, pipe, stage, reflection
                    )

                    stages[str(stage)] = stage_info

            pipeline_info["shaders"] = stages

            # Try D3D11-specific state access
            d3d11_state = None
            try:
                d3d11_state = controller.GetD3D11PipelineState()
            except Exception:
                pass

            # Viewport and scissor
            try:
                vp_scissor = pipe.GetViewportScissor()
                if vp_scissor:
                    viewports = []
                    for v in vp_scissor.viewports:
                        viewports.append(
                            {
                                "x": v.x,
                                "y": v.y,
                                "width": v.width,
                                "height": v.height,
                                "min_depth": v.minDepth,
                                "max_depth": v.maxDepth,
                            }
                        )
                    pipeline_info["viewports"] = viewports
            except Exception as e:
                pipeline_info["viewports_error"] = str(e)

            # Render targets - try D3D11-specific OM state first
            try:
                if d3d11_state:
                    om = d3d11_state.outputMerger
                    if om:
                        rts = []
                        for i, rt in enumerate(om.renderTargets):
                            res_id = getattr(rt, 'resource', getattr(rt, 'resourceId', rd.ResourceId.Null()))
                            if res_id != rd.ResourceId.Null():
                                rt_info = {"index": i, "resource_id": str(res_id)}
                                # Get RTV view format
                                try:
                                    fmt = rt.format
                                    if fmt:
                                        rt_info["view_format"] = str(fmt.Name())
                                except Exception:
                                    pass
                                # Get slice/mip info
                                for attr in ['firstMip', 'firstSlice', 'numMips', 'numSlices']:
                                    val = getattr(rt, attr, None)
                                    if val is not None:
                                        rt_info[attr] = val
                                # Get texture info
                                tex = tex_map.get(str(res_id))
                                if tex is not None:
                                    rt_info["texture_format"] = str(tex.format.Name())
                                    rt_info["width"] = tex.width
                                    rt_info["height"] = tex.height
                                rts.append(rt_info)
                        pipeline_info["render_targets"] = rts

                        try:
                            ds = om.depthTarget
                            depth_id = getattr(ds, 'resource', getattr(ds, 'resourceId', rd.ResourceId.Null()))
                            if depth_id != rd.ResourceId.Null():
                                pipeline_info["depth_target"] = str(depth_id)
                        except Exception:
                            pass
                else:
                    # Fall back to abstract pipe
                    om = pipe.GetOutputMerger()
                    if om:
                        rts = []
                        for i, rt in enumerate(om.renderTargets):
                            res_id = getattr(rt, 'resource', getattr(rt, 'resourceId', rd.ResourceId.Null()))
                            if res_id != rd.ResourceId.Null():
                                rt_info = {"index": i, "resource_id": str(res_id)}
                                tex = tex_map.get(str(res_id))
                                if tex is not None:
                                    rt_info["format"] = str(tex.format.Name())
                                    rt_info["width"] = tex.width
                                    rt_info["height"] = tex.height
                                rts.append(rt_info)
                        pipeline_info["render_targets"] = rts
            except Exception as e:
                pipeline_info["render_targets_error"] = str(e)

            # Input assembly
            try:
                if d3d11_state:
                    ia = d3d11_state.inputAssembly
                    if ia:
                        ia_info = {"topology": str(ia.topology)}

                        # Index buffer details
                        try:
                            idx = getattr(ia, "indexBuffer", None)
                            if idx is not None:
                                idx_desc = getattr(idx, "descriptor", idx)
                                idx_res = getattr(
                                    idx_desc,
                                    "resourceId",
                                    getattr(idx_desc, "resource", rd.ResourceId.Null()),
                                )
                                idx_info = {}
                                if idx_res != rd.ResourceId.Null():
                                    idx_info["resource_id"] = str(idx_res)
                                for attr in [
                                    "byteOffset",
                                    "offset",
                                    "byteStride",
                                    "indexByteStride",
                                    "format",
                                ]:
                                    val = getattr(idx_desc, attr, None)
                                    if val is not None:
                                        idx_info[attr] = str(val) if attr == "format" else val
                                if idx_info:
                                    ia_info["index_buffer"] = idx_info
                        except Exception as e:
                            ia_info["index_buffer_error"] = str(e)

                        # Vertex buffer details
                        try:
                            vb_entries = []
                            for slot, vb in enumerate(getattr(ia, "vertexBuffers", [])):
                                vb_desc = getattr(vb, "descriptor", vb)
                                vb_info = {"slot": slot}
                                vb_res = getattr(
                                    vb_desc,
                                    "resourceId",
                                    getattr(vb_desc, "resource", rd.ResourceId.Null()),
                                )
                                if vb_res != rd.ResourceId.Null():
                                    vb_info["resource_id"] = str(vb_res)
                                for attr in [
                                    "byteOffset",
                                    "offset",
                                    "byteStride",
                                    "stride",
                                    "perInstance",
                                    "instanceRate",
                                    "instanceDataStepRate",
                                ]:
                                    val = getattr(vb_desc, attr, None)
                                    if val is not None:
                                        vb_info[attr] = val
                                vb_entries.append(vb_info)
                            ia_info["vertex_buffers"] = vb_entries
                        except Exception as e:
                            ia_info["vertex_buffers_error"] = str(e)

                        # Input layout elements (if exposed by current RenderDoc build)
                        try:
                            elems = []
                            for elem in getattr(ia, "layouts", []):
                                entry = {}
                                for attr in [
                                    "semanticName",
                                    "semanticIndex",
                                    "format",
                                    "byteOffset",
                                    "vertexBuffer",
                                    "vertexBufferSlot",
                                    "perInstance",
                                    "instanceRate",
                                    "instanceDataStepRate",
                                ]:
                                    val = getattr(elem, attr, None)
                                    if val is not None:
                                        entry[attr] = str(val) if attr == "format" else val
                                if entry:
                                    elems.append(entry)
                            if elems:
                                ia_info["layout_elements"] = elems
                        except Exception as e:
                            ia_info["layout_error"] = str(e)

                        pipeline_info["input_assembly"] = ia_info
                else:
                    ia = pipe.GetIAState()
                    if ia:
                        pipeline_info["input_assembly"] = {"topology": str(ia.topology)}
            except Exception as e:
                pipeline_info["input_assembly_error"] = str(e)

            # D3D11 rasterizer state (viewport)
            if d3d11_state and not pipeline_info.get("viewports"):
                try:
                    rs = d3d11_state.rasterizer
                    if rs:
                        viewports = []
                        for v in rs.viewports:
                            viewports.append({
                                "x": v.x, "y": v.y,
                                "width": v.width, "height": v.height,
                                "min_depth": v.minDepth, "max_depth": v.maxDepth,
                            })
                        pipeline_info["viewports"] = viewports
                except Exception:
                    pass

            # D3D11 fixed-function state details (rasterizer / OM)
            if d3d11_state:
                try:
                    pipeline_info["_debug_d3d11_attrs"] = [
                        name for name in dir(d3d11_state) if "shader" in name.lower() or "input" in name.lower() or "output" in name.lower() or "raster" in name.lower()
                    ]
                    pipeline_info["_debug_d3d11_attr_types"] = {
                        name: type(getattr(d3d11_state, name)).__name__
                        for name in pipeline_info["_debug_d3d11_attrs"]
                    }
                except Exception:
                    pass
                try:
                    stage_bindings = {}
                    stage_attrs = [
                        ("vertex", "vertexShader"),
                        ("pixel", "pixelShader"),
                        ("geometry", "geometryShader"),
                        ("hull", "hullShader"),
                        ("domain", "domainShader"),
                        ("compute", "computeShader"),
                    ]
                    for stage_name, attr_name in stage_attrs:
                        stage_obj = getattr(d3d11_state, attr_name, None)
                        if not stage_obj:
                            # Try alternate naming used by some RenderDoc forks.
                            alt = attr_name[:1].upper() + attr_name[1:]
                            stage_obj = getattr(d3d11_state, alt, None)
                        if stage_obj:
                            try:
                                pipeline_info["_debug_stage_attrs_%s" % stage_name] = list(dir(stage_obj))
                            except Exception:
                                pass
                        if not stage_obj:
                            continue
                        cb_list = []
                        for slot, cb in enumerate(getattr(stage_obj, "constantBuffers", [])):
                            cb_desc = getattr(cb, "descriptor", cb)
                            res_id = getattr(
                                cb_desc,
                                "resourceId",
                                getattr(cb_desc, "resource", rd.ResourceId.Null()),
                            )
                            entry = {"slot": slot}
                            if res_id != rd.ResourceId.Null():
                                entry["resource_id"] = str(res_id)
                            for cb_attr in ["byteOffset", "offset", "byteSize", "size"]:
                                val = getattr(cb_desc, cb_attr, None)
                                if val is not None:
                                    entry[cb_attr] = int(val)
                            cb_list.append(entry)
                        if cb_list:
                            stage_bindings[stage_name] = cb_list
                    if stage_bindings:
                        pipeline_info["d3d11_constant_buffer_bindings"] = stage_bindings
                except Exception as e:
                    pipeline_info["d3d11_constant_buffer_bindings_error"] = str(e)

                try:
                    rs = d3d11_state.rasterizer
                    if rs:
                        rs_info = {}
                        for attr in [
                            "fillMode",
                            "cullMode",
                            "frontCCW",
                            "depthClamp",
                            "depthClip",
                            "scissorEnable",
                            "ScissorEnable",
                            "scissorEnabled",
                            "multisampleEnable",
                            "antialiasedLines",
                            "forcedSampleCount",
                        ]:
                            val = getattr(rs, attr, None)
                            if val is not None:
                                key = attr
                                if attr in ("ScissorEnable", "scissorEnabled"):
                                    key = "scissorEnable"
                                rs_info[key] = str(val) if hasattr(val, "name") else val

                        try:
                            scissors = []
                            for s in getattr(rs, "scissors", []):
                                x = int(getattr(s, "x", getattr(s, "left", 0)))
                                y = int(getattr(s, "y", getattr(s, "top", 0)))
                                w = int(getattr(s, "w", 0))
                                h = int(getattr(s, "h", 0))
                                right = int(getattr(s, "right", x + w))
                                bottom = int(getattr(s, "bottom", y + h))
                                scissors.append(
                                    {
                                        "left": x,
                                        "top": y,
                                        "right": right,
                                        "bottom": bottom,
                                    }
                                )
                            if scissors:
                                rs_info["scissors"] = scissors
                        except Exception as e:
                            rs_info["scissors_error"] = str(e)

                        try:
                            rs_info["_debug_attrs"] = [
                                name
                                for name in dir(rs)
                                if ("scissor" in name.lower() or "depth" in name.lower())
                            ]
                        except Exception:
                            pass

                        if rs_info:
                            pipeline_info["rasterizer_state"] = rs_info
                except Exception as e:
                    pipeline_info["rasterizer_state_error"] = str(e)

                try:
                    om = d3d11_state.outputMerger
                    if om:
                        om_info = {}

                        try:
                            ds = getattr(om, "depthState", None)
                            if ds is None:
                                ds = getattr(om, "DepthState", None)
                            if ds is not None:
                                ds_info = {}
                                for attr in [
                                    "depthEnable",
                                    "DepthEnable",
                                    "depthWrites",
                                    "DepthWrites",
                                    "depthFunction",
                                    "DepthFunction",
                                    "depthBounds",
                                    "DepthBounds",
                                    "stencilEnable",
                                    "StencilEnable",
                                    "stencilReadMask",
                                    "StencilReadMask",
                                    "stencilWriteMask",
                                    "StencilWriteMask",
                                ]:
                                    val = getattr(ds, attr, None)
                                    if val is not None:
                                        key = attr[0].lower() + attr[1:] if attr[:1].isupper() else attr
                                        ds_info[key] = str(val) if hasattr(val, "name") else val
                                try:
                                    ds_info["_debug_attrs"] = [
                                        name
                                        for name in dir(ds)
                                        if ("depth" in name.lower() or "stencil" in name.lower())
                                    ]
                                except Exception:
                                    pass
                                if ds_info:
                                    om_info["depth_stencil_state"] = ds_info
                        except Exception as e:
                            om_info["depth_stencil_state_error"] = str(e)

                        try:
                            bs = getattr(om, "blendState", None)
                            if bs is not None:
                                bs_info = {}
                                alpha_to_cov = getattr(bs, "alphaToCoverage", None)
                                if alpha_to_cov is not None:
                                    bs_info["alphaToCoverage"] = alpha_to_cov
                                indep = getattr(bs, "independentBlend", None)
                                if indep is not None:
                                    bs_info["independentBlend"] = indep

                                targets = []
                                for i, bt in enumerate(getattr(bs, "blends", [])):
                                    t = {"index": i}
                                    for attr in [
                                        "enabled",
                                        "logicEnabled",
                                        "logic",
                                        "writeMask",
                                        "source",
                                        "destination",
                                        "operation",
                                        "alphaSource",
                                        "alphaDestination",
                                        "alphaOperation",
                                    ]:
                                        val = getattr(bt, attr, None)
                                        if val is not None:
                                            t[attr] = str(val) if hasattr(val, "name") else val
                                    targets.append(t)
                                if targets:
                                    bs_info["targets"] = targets
                                if bs_info:
                                    om_info["blend_state"] = bs_info
                        except Exception as e:
                            om_info["blend_state_error"] = str(e)

                        try:
                            sample_mask = getattr(om, "sampleMask", None)
                            if sample_mask is not None:
                                om_info["sample_mask"] = int(sample_mask)
                        except Exception:
                            pass

                        if om_info:
                            pipeline_info["output_merger_state"] = om_info
                except Exception as e:
                    pipeline_info["output_merger_state_error"] = str(e)

            result["pipeline"] = pipeline_info

        self._invoke(callback)

        if result["error"]:
            raise ValueError(result["error"])
        return result["pipeline"]

    def _get_stage_resources(self, pipe, stage, reflection, tex_map, buf_map):
        """Get shader resource views (SRVs) for a stage"""
        resources = []
        try:
            srvs = pipe.GetReadOnlyResources(stage, False)

            name_map = {}
            if reflection:
                for res in reflection.readOnlyResources:
                    bind = getattr(res, 'fixedBindNumber', getattr(res, 'bindPoint', -1))
                    name_map[bind] = res.name

            for srv in srvs:
                desc = srv.descriptor
                res_id = getattr(desc, 'resource', rd.ResourceId.Null())
                if res_id == rd.ResourceId.Null():
                    continue

                slot = srv.access.index
                res_info = {
                    "slot": slot,
                    "name": name_map.get(slot, ""),
                    "resource_id": str(res_id),
                }

                res_info.update(
                    self._get_resource_details(res_id, tex_map, buf_map)
                )

                res_info["first_mip"] = getattr(desc, 'firstMip', 0)
                res_info["num_mips"] = getattr(desc, 'numMips', 1)
                res_info["first_slice"] = getattr(desc, 'firstSlice', 0)
                res_info["num_slices"] = getattr(desc, 'numSlices', 1)

                resources.append(res_info)
        except Exception as e:
            resources.append({"error": str(e)})

        return resources

    def _get_stage_uavs(self, pipe, stage, reflection, tex_map, buf_map):
        """Get unordered access views (UAVs) for a stage"""
        uavs = []
        try:
            uav_list = pipe.GetReadWriteResources(stage, False)

            name_map = {}
            if reflection:
                for res in reflection.readWriteResources:
                    bind = getattr(res, 'fixedBindNumber', getattr(res, 'bindPoint', -1))
                    name_map[bind] = res.name

            for uav in uav_list:
                desc = uav.descriptor
                res_id = getattr(desc, 'resource', rd.ResourceId.Null())
                if res_id == rd.ResourceId.Null():
                    continue

                slot = uav.access.index
                uav_info = {
                    "slot": slot,
                    "name": name_map.get(slot, ""),
                    "resource_id": str(res_id),
                }

                uav_info.update(
                    self._get_resource_details(res_id, tex_map, buf_map)
                )

                uav_info["first_element"] = getattr(desc, 'firstMip', 0)
                uav_info["num_elements"] = getattr(desc, 'numMips', 0)

                uavs.append(uav_info)
        except Exception as e:
            uavs.append({"error": str(e)})

        return uavs

    def _get_stage_samplers(self, pipe, stage, reflection):
        """Get samplers for a stage"""
        samplers = []
        try:
            sampler_list = pipe.GetSamplers(stage, False)

            name_map = {}
            if reflection:
                for samp in reflection.samplers:
                    name_map[samp.fixedBindNumber] = samp.name

            for samp in sampler_list:
                slot = samp.access.index
                samp_info = {
                    "slot": slot,
                    "name": name_map.get(slot, ""),
                }

                desc = samp.descriptor
                try:
                    samp_info["address_u"] = str(desc.addressU)
                    samp_info["address_v"] = str(desc.addressV)
                    samp_info["address_w"] = str(desc.addressW)
                except AttributeError:
                    pass

                try:
                    samp_info["filter"] = str(desc.filter)
                except AttributeError:
                    pass

                try:
                    samp_info["max_anisotropy"] = desc.maxAnisotropy
                except AttributeError:
                    pass

                try:
                    samp_info["min_lod"] = desc.minLOD
                    samp_info["max_lod"] = desc.maxLOD
                    samp_info["mip_lod_bias"] = desc.mipLODBias
                except AttributeError:
                    pass

                try:
                    samp_info["border_color"] = [
                        desc.borderColor[0],
                        desc.borderColor[1],
                        desc.borderColor[2],
                        desc.borderColor[3],
                    ]
                except (AttributeError, TypeError):
                    pass

                try:
                    samp_info["compare_function"] = str(desc.compareFunction)
                except AttributeError:
                    pass

                samplers.append(samp_info)
        except Exception as e:
            samplers.append({"error": str(e)})

        return samplers

    def _get_stage_cbuffers(self, controller, pipe, stage, reflection):
        """Get constant buffers for a stage from shader reflection"""
        cbuffers = []
        try:
            if not reflection:
                return cbuffers

            for cb in reflection.constantBlocks:
                slot = cb.bindPoint if hasattr(cb, 'bindPoint') else cb.fixedBindNumber
                cb_info = {
                    "slot": slot,
                    "name": cb.name,
                    "byte_size": cb.byteSize,
                    "variable_count": len(cb.variables) if cb.variables else 0,
                    "variables": [],
                }
                if cb.variables:
                    for var in cb.variables:
                        cb_info["variables"].append({
                            "name": var.name,
                            "byte_offset": var.byteOffset,
                            "type": str(var.type.name) if var.type else "",
                        })
                cbuffers.append(cb_info)

        except Exception as e:
            cbuffers.append({"error": str(e)})

        return cbuffers

    @staticmethod
    def _build_resource_maps(controller):
        """Build resource ID -> info lookup dicts (call once per BlockInvoke).
        Uses str(resourceId) as keys since ResourceId may not be hashable."""
        tex_map = {}
        for tex in controller.GetTextures():
            tex_map[str(tex.resourceId)] = tex
        buf_map = {}
        for buf in controller.GetBuffers():
            buf_map[str(buf.resourceId)] = buf
        return tex_map, buf_map

    def _get_resource_details(self, resource_id, tex_map, buf_map):
        """Get details about a resource using pre-built lookup maps"""
        details = {}

        try:
            resource_name = self.ctx.GetResourceName(resource_id)
            if resource_name:
                details["resource_name"] = resource_name
        except Exception:
            pass

        key = str(resource_id)
        tex = tex_map.get(key)
        if tex is not None:
            details["type"] = "texture"
            details["width"] = tex.width
            details["height"] = tex.height
            details["depth"] = tex.depth
            details["array_size"] = tex.arraysize
            details["mip_levels"] = tex.mips
            details["format"] = str(tex.format.Name())
            details["dimension"] = str(tex.type)
            details["msaa_samples"] = tex.msSamp
            return details

        buf = buf_map.get(key)
        if buf is not None:
            details["type"] = "buffer"
            details["length"] = buf.length
            return details

        return details

    def _get_cbuffer_info(self, controller, pipe, reflection, stage):
        """Get constant buffer information and values"""
        cbuffers = []
        get_constant_buffer = getattr(pipe, "GetConstantBuffer", None)
        can_read_values = callable(get_constant_buffer)

        for i, cb in enumerate(reflection.constantBlocks):
            cb_info = {
                "name": cb.name,
                "slot": i,
                "size": cb.byteSize,
                "variables": [],
            }

            if can_read_values:
                try:
                    bind = get_constant_buffer(stage, i, 0)
                    if bind.resourceId != rd.ResourceId.Null():
                        variables = controller.GetCBufferVariableContents(
                            pipe.GetGraphicsPipelineObject(),
                            reflection.resourceId,
                            stage,
                            reflection.entryPoint,
                            i,
                            bind.resourceId,
                            bind.byteOffset,
                            bind.byteSize,
                        )
                        cb_info["variables"] = Serializers.serialize_variables(variables)
                except Exception as e:
                    cb_info["error"] = str(e)
            else:
                cb_info["values_unavailable"] = (
                    "PipeState.GetConstantBuffer is unavailable in this RenderDoc build."
                )

            cbuffers.append(cb_info)

        return cbuffers

    def _get_resource_bindings(self, reflection):
        """Get shader resource bindings"""
        resources = []

        try:
            for res in reflection.readOnlyResources:
                resources.append(
                    {
                        "name": res.name,
                        "type": str(res.resType),
                        "binding": res.fixedBindNumber,
                        "access": "ReadOnly",
                    }
                )
        except Exception:
            pass

        try:
            for res in reflection.readWriteResources:
                resources.append(
                    {
                        "name": res.name,
                        "type": str(res.resType),
                        "binding": res.fixedBindNumber,
                        "access": "ReadWrite",
                    }
                )
        except Exception:
            pass

        return resources
