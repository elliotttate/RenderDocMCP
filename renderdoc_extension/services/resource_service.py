"""
Resource information service for RenderDoc.
"""

import base64

import renderdoc as rd

from ..utils import Parsers


class ResourceService:
    """Resource information service"""

    def __init__(self, ctx, invoke_fn):
        self.ctx = ctx
        self._invoke = invoke_fn

    def _find_texture_by_id(self, controller, resource_id):
        """Find texture by resource ID"""
        target_id = Parsers.extract_numeric_id(resource_id)
        for tex in controller.GetTextures():
            tex_id_str = str(tex.resourceId)
            tex_id = Parsers.extract_numeric_id(tex_id_str)
            if tex_id == target_id:
                return tex
        return None

    def get_buffer_contents(self, resource_id, offset=0, length=0, event_id=None):
        """Get buffer data"""
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("No capture loaded")

        result = {"data": None, "error": None}

        def callback(controller):
            if event_id is not None:
                try:
                    controller.SetFrameEvent(int(event_id), True)
                except Exception as e:
                    result["error"] = "Invalid event_id %s: %s" % (str(event_id), str(e))
                    return

            # Parse resource ID
            try:
                target_id = Parsers.extract_numeric_id(resource_id)
            except Exception:
                result["error"] = "Invalid resource ID: %s" % resource_id
                return

            # Find buffer
            buf_desc = None
            rid = rd.ResourceId.Null()
            for buf in controller.GetBuffers():
                try:
                    buf_id = Parsers.extract_numeric_id(str(buf.resourceId))
                except Exception:
                    continue
                if buf_id == target_id:
                    buf_desc = buf
                    rid = buf.resourceId
                    break

            if not buf_desc:
                # Fallback: try direct parse path for forks where GetBuffers()
                # list is incomplete but GetBufferData accepts the ID.
                try:
                    rid = Parsers.parse_resource_id(resource_id)
                    data = controller.GetBufferData(rid, offset, length if length > 0 else 0)
                    result["data"] = {
                        "resource_id": resource_id,
                        "event_id": event_id,
                        "length": len(data),
                        "total_size": len(data),
                        "offset": offset,
                        "content_base64": base64.b64encode(data).decode("ascii"),
                        "buffer_lookup": "direct_fallback",
                    }
                except Exception:
                    result["error"] = "Buffer not found: %s" % resource_id
                return

            # Get data
            actual_length = length if length > 0 else buf_desc.length
            data = controller.GetBufferData(rid, offset, actual_length)

            result["data"] = {
                "resource_id": resource_id,
                "event_id": event_id,
                "length": len(data),
                "total_size": buf_desc.length,
                "offset": offset,
                "content_base64": base64.b64encode(data).decode("ascii"),
            }

        self._invoke(callback)

        if result["error"]:
            raise ValueError(result["error"])
        return result["data"]

    def get_texture_info(self, resource_id):
        """Get texture metadata"""
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("No capture loaded")

        result = {"texture": None, "error": None}

        def callback(controller):
            try:
                tex_desc = self._find_texture_by_id(controller, resource_id)

                if not tex_desc:
                    result["error"] = "Texture not found: %s" % resource_id
                    return

                tex_info = {
                    "resource_id": resource_id,
                    "name": "",
                    "width": tex_desc.width,
                    "height": tex_desc.height,
                    "depth": tex_desc.depth,
                    "array_size": tex_desc.arraysize,
                    "mip_levels": tex_desc.mips,
                    "format": str(tex_desc.format.Name()),
                    "dimension": str(tex_desc.type),
                    "msaa_samples": tex_desc.msSamp,
                }
                try:
                    tex_info["byte_size"] = int(tex_desc.byteSize)
                except Exception:
                    pass
                try:
                    # Get resource name safely
                    rname = self.ctx.GetResourceName(tex_desc.resourceId)
                    if rname:
                        # Sanitize: remove non-ASCII chars that could break JSON
                        tex_info["name"] = rname.encode('ascii', 'replace').decode('ascii')
                except Exception:
                    pass
                result["texture"] = tex_info
            except Exception as e:
                import traceback
                result["error"] = "Error: %s\n%s" % (str(e), traceback.format_exc())

        self._invoke(callback)

        if result["error"]:
            raise ValueError(result["error"])
        return result["texture"]

    def get_texture_data(
        self,
        resource_id,
        mip=0,
        slice=0,
        sample=0,
        depth_slice=None,
        event_id=None,
    ):
        """Get texture pixel data."""
        if not self.ctx.IsCaptureLoaded():
            raise ValueError("No capture loaded")

        result = {"data": None, "error": None}

        def callback(controller):
            if event_id is not None:
                try:
                    controller.SetFrameEvent(int(event_id), True)
                except Exception as e:
                    result["error"] = "Invalid event_id %s: %s" % (str(event_id), str(e))
                    return

            tex_desc = self._find_texture_by_id(controller, resource_id)

            if not tex_desc:
                result["error"] = "Texture not found: %s" % resource_id
                return

            # Validate mip level
            if mip < 0 or mip >= tex_desc.mips:
                result["error"] = "Invalid mip level %d (texture has %d mips)" % (
                    mip,
                    tex_desc.mips,
                )
                return

            # Validate slice for array/cube textures
            max_slices = tex_desc.arraysize
            if tex_desc.cubemap:
                max_slices = tex_desc.arraysize * 6
            if slice < 0 or (max_slices > 1 and slice >= max_slices):
                result["error"] = "Invalid slice %d (texture has %d slices)" % (
                    slice,
                    max_slices,
                )
                return

            # Validate sample for MSAA
            if sample < 0 or (tex_desc.msSamp > 1 and sample >= tex_desc.msSamp):
                result["error"] = "Invalid sample %d (texture has %d samples)" % (
                    sample,
                    tex_desc.msSamp,
                )
                return

            # Calculate dimensions at this mip level
            mip_width = max(1, tex_desc.width >> mip)
            mip_height = max(1, tex_desc.height >> mip)
            mip_depth = max(1, tex_desc.depth >> mip)

            # Validate depth_slice for 3D textures
            is_3d = tex_desc.depth > 1
            if depth_slice is not None:
                if not is_3d:
                    result["error"] = "depth_slice can only be used with 3D textures"
                    return
                if depth_slice < 0 or depth_slice >= mip_depth:
                    result["error"] = "Invalid depth_slice %d (texture has %d depth at mip %d)" % (
                        depth_slice,
                        mip_depth,
                        mip,
                    )
                    return

            # Create subresource specification
            sub = rd.Subresource()
            sub.mip = mip
            sub.slice = slice
            sub.sample = sample

            # Get texture data
            try:
                data = controller.GetTextureData(tex_desc.resourceId, sub)
            except Exception as e:
                result["error"] = "Failed to get texture data: %s" % str(e)
                return

            # Extract depth slice for 3D textures if requested
            output_depth = mip_depth
            if is_3d and depth_slice is not None:
                total_size = len(data)
                bytes_per_slice = total_size // mip_depth
                slice_start = depth_slice * bytes_per_slice
                slice_end = slice_start + bytes_per_slice
                data = data[slice_start:slice_end]
                output_depth = 1

            result["data"] = {
                "resource_id": resource_id,
                "event_id": event_id,
                "width": mip_width,
                "height": mip_height,
                "depth": output_depth,
                "mip": mip,
                "slice": slice,
                "sample": sample,
                "depth_slice": depth_slice,
                "format": str(tex_desc.format.Name()),
                "dimension": str(tex_desc.type),
                "is_3d": is_3d,
                "total_depth": mip_depth if is_3d else 1,
                "data_length": len(data),
                "content_base64": base64.b64encode(data).decode("ascii"),
            }

        self._invoke(callback)

        if result["error"]:
            raise ValueError(result["error"])
        return result["data"]
