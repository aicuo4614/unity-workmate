"""在 qrenderdoc --python 的内置 Python 中运行；与服务端 Python ABI 隔离。"""
import json
import os
import sys
import traceback
from pathlib import Path
import renderdoc as rd

def ok(status):
    return status.OK() if hasattr(status, "OK") else status == rd.ResultCode.Succeeded

def encode(obj, depth=0):
    if obj is None or type(obj) in (str, int, float, bool):
        return obj
    if isinstance(obj, rd.ResourceId):
        return str(obj)
    if isinstance(obj, (int, float)):
        return str(obj)
    if depth > 6:
        return {"omitted": "nested_value_depth_limit"}
    if (hasattr(obj, "__iter__") or (hasattr(obj, "__len__") and hasattr(obj, "__getitem__"))) and not isinstance(obj, (str, bytes, dict)):
        try:
            return [encode(v, depth+1) for v in obj]
        except TypeError:
            pass
    result = {}
    for key in dir(obj):
        if key.startswith("_") or key in ("this", "thisown"):
            continue
        try:
            v = getattr(obj, key)
            if not callable(v):
                result[key] = encode(v, depth+1)
        except Exception:
            pass
    return result if result else (None if "Swig Object" in str(obj) else str(obj))

def main(req):
    cap, controller, remote = rd.OpenCaptureFile(), None, None
    result = {"ok":False, "capture":{}, "events":[], "draws":[], "resources":[],
              "shaders":[], "images":[], "limitations":[], "coverage":{}}
    outdir = Path(req["directory"])
    try:
        status = cap.OpenFile(req["path"], "", None)
        if not ok(status):
            raise RuntimeError("OpenFile: " + str(status))
        result["capture"]["api"] = str(cap.DriverName())
        try:
            thumb = cap.GetThumbnail(rd.FileType.PNG, 1024)
            if thumb:
                (outdir/"thumbnail.png").write_bytes(bytes(thumb.data))
                result["images"].append({"file":"thumbnail.png","kind":"embedded_thumbnail","event_id":None})
        except Exception as exc:
            result["limitations"].append("Thumbnail: " + str(exc))
        if req.get("remote_host"):
            status, remote = rd.CreateRemoteServerConnection(req["remote_host"])
            if not ok(status):
                raise RuntimeError("Remote connection: " + str(status))
            remote_path = remote.CopyCaptureToRemote(req["path"], None)
            status, controller = remote.OpenCapture(rd.RemoteServer.NoPreference, remote_path, rd.ReplayOptions(), None)
        else:
            status, controller = cap.OpenCapture(rd.ReplayOptions(), None)
        if not ok(status) or controller is None:
            raise RuntimeError("Replay: " + str(status))
        result["capture"]["replay_host"] = req.get("remote_host") or "local"
        result["capture"]["replay_api_properties"] = encode(controller.GetAPIProperties())
        descriptions = controller.GetResources()
        names = {str(r.resourceId):r.name for r in descriptions}
        structured = controller.GetStructuredFile()
        raw_ids = {}
        for kind, descriptions in (("texture",controller.GetTextures()), ("buffer",controller.GetBuffers())):
            for r in descriptions:
                rid = str(r.resourceId)
                raw_ids[rid] = r.resourceId
                row = {"id":rid,"name":names.get(rid,""),"kind":kind,
                       "size_bytes":int(r.byteSize if kind == "texture" else r.length),"size_basis":"renderdoc_description_byte_size",
                       "usage":None}
                if kind == "texture":
                    row.update(width=int(r.width),height=int(r.height),depth=int(r.depth),
                               array_size=int(r.arraysize),mips=int(r.mips),samples=int(r.msSamp),
                               format=r.format.Name(),cubemap=bool(r.cubemap))
                result["resources"].append(row)
        pipeline_failures = []
        def walk(actions, path, parent=None):
            for a in actions:
                eid = int(a.eventId)
                name = a.GetName(structured)
                kind = "draw" if a.flags & rd.ActionFlags.Drawcall else (
                    "dispatch" if a.flags & rd.ActionFlags.Dispatch else "event")
                result["events"].append({"event_id":eid,"chunk_index":int(a.events[-1].chunkIndex) if a.events else None,"parent_event_id":parent,
                                         "name":name,"kind":kind,"path":path,"flags":str(a.flags)})
                if kind in ("draw","dispatch"):
                    row = {"event_id":eid,"chunk_index":int(a.events[-1].chunkIndex) if a.events else None,"name":name,"kind":kind,"path":path,
                           "count":int(a.numIndices),"instances":int(a.numInstances),
                           "triangles":None,"gpu_ms":None,"inputs":[],"outputs":[],"pipeline":None,
                           "shader_id":None,"shader_name":""}
                    try:
                        controller.SetFrameEvent(eid, True)
                        pipe = controller.GetPipelineState()
                        topology = str(pipe.GetPrimitiveTopology())
                        row["topology"] = topology
                        if kind == "draw":
                            if topology.endswith("TriangleList"): row["triangles"]=int(a.numIndices)//3*int(a.numInstances)
                            elif topology.endswith(("TriangleStrip","TriangleFan")):
                                row["triangles"]=max(0,int(a.numIndices)-2)*int(a.numInstances)
                        stage = rd.ShaderStage.Compute if kind == "dispatch" else rd.ShaderStage.Pixel
                        sid = pipe.GetShader(stage)
                        row["shader_id"], row["shader_name"] = str(sid),names.get(str(sid),"")
                        row["outputs"] = [str(o.resource) for o in pipe.GetOutputTargets()
                                          if o.resource != rd.ResourceId()]
                        row["depth_target"] = str(pipe.GetDepthTarget().resource)
                        descriptors = pipe.GetReadOnlyResources(stage)
                        for desc in descriptors:
                            descriptor = getattr(desc,"descriptor",None)
                            if descriptor is not None:
                                row["inputs"].append(str(descriptor.resource))
                            else:
                                for resource in getattr(desc,"resources",[]):
                                    row["inputs"].append(str(getattr(resource,"resourceId",rd.ResourceId())))
                        api = str(controller.GetAPIProperties().pipelineType)
                        native = controller.GetGLPipelineState() if "OpenGL" in api else (
                            controller.GetVulkanPipelineState() if "Vulkan" in api else (
                            controller.GetD3D11PipelineState() if "D3D11" in api else controller.GetD3D12PipelineState()))
                        if "OpenGL" in api:
                            binding = native.computeShader if kind == "dispatch" else native.fragmentShader
                            program_id = getattr(binding, "programResourceId", rd.ResourceId())
                            row["program_id"] = str(program_id)
                            row["shader_name"] = names.get(str(program_id), row["shader_name"])
                        row["pipeline"] = {k:encode(getattr(native,k)) for k in
                            ("rasterizer","depthState","stencilState","blendState","framebuffer","depthStencilState","outputMerger")
                            if hasattr(native,k)}
                    except Exception as exc:
                        pipeline_failures.append({"event_id":eid,"error":str(exc)})
                    if row["pipeline"] is not None:
                        try:
                            row["shader_bindings"], row["inputs"], row["writes"], row["constant_blocks"] = [], [], [], []
                            for stage_name in ("Vertex", "Hull", "Domain", "Geometry", "Pixel", "Compute"):
                                if not hasattr(rd.ShaderStage, stage_name):
                                    continue
                                bound_stage = getattr(rd.ShaderStage, stage_name)
                                shader = pipe.GetShader(bound_stage)
                                if shader == rd.ResourceId():
                                    continue
                                row["shader_bindings"].append({"stage":stage_name, "id":str(shader),
                                    "name":names.get(str(shader),""), "entry_point":str(pipe.GetShaderEntryPoint(bound_stage))})
                                for query, target in ((pipe.GetReadOnlyResources, "inputs"),
                                                       (pipe.GetReadWriteResources, "writes"),
                                                       (pipe.GetConstantBlocks, "constant_blocks")):
                                    for bound in query(bound_stage):
                                        row[target].append({"stage":stage_name, "binding":encode(bound)})
                        except Exception as exc:
                            pipeline_failures.append({"event_id":eid, "error":"Stage bindings: "+str(exc)})
                    result["draws"].append(row)
                walk(a.children,path+[name] if a.children else path,eid)
        walk(controller.GetRootActions(),[])
        for row in result["resources"]:
            try:
                row["usage"] = [{"event_id":int(u.eventId),"usage":str(u.usage)}
                                for u in controller.GetUsage(raw_ids[row["id"]])]
            except Exception as exc:
                result["limitations"].append("Resource usage " + row["id"] + ": " + str(exc))
        shaders = {row.get("shader_id"):row.get("shader_name") for row in result["draws"] if row.get("shader_id")}
        result["shaders"] = [{"id":rid,"name":name} for rid,name in shaders.items()]
        # 明确这是末次绘制输出，不能冒称系统合成后的屏幕图。
        last = next((r for r in reversed(result["draws"]) if r.get("outputs")),None)
        if last:
            try:
                controller.SetFrameEvent(last["event_id"],True)
                rid = raw_ids.get(last["outputs"][0])
                if rid is not None:
                    save = rd.TextureSave()
                    save.resourceId, save.destType, save.mip = rid, rd.FileType.PNG, 0
                    status = controller.SaveTexture(save,str(outdir/"last-output.png"))
                    if ok(status):
                        result["images"].append({"file":"last-output.png","kind":"last_draw_output",
                                                 "event_id":last["event_id"],"resource_id":str(rid)})
            except Exception as exc:
                result["limitations"].append("Output image: " + str(exc))
        result["coverage"] = {"mode":"replay","draw_api_supported":True,"event_ids":True,
            "pipeline_failures":pipeline_failures,"resource_count":len(result["resources"])}
        result["ok"] = True
        result["limitations"].append("未采集 GPU 计时；管线/读写关系来自回放设备，不代表原设备耗时。")
        result["limitations"].append("此版本未导出 Shader 源码；原始抓帧保留全部数据。")
    except Exception as exc:
        result["error"] = str(exc)
        result["traceback"] = traceback.format_exc()
    finally:
        if controller:
            if remote: remote.CloseCapture(controller)
            else: controller.Shutdown()
        if remote: remote.ShutdownConnection()
        cap.Shutdown()
        (outdir/"replay-result.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")

main(json.loads(Path(os.environ["WORKMATE_RENDERDOC_REQUEST"]).read_text(encoding="utf-8-sig")))
# 转换专用进程：释放回放句柄后退出，防止 qrenderdoc 继续打开交互窗口。
os._exit(0)
