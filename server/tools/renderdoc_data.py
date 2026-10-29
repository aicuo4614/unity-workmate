"""RDC 明细与确定性摘要；不依赖大模型。"""
from __future__ import annotations
import hashlib
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temp.replace(path)

def source_info(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for part in iter(lambda: stream.read(1048576), b""):
            digest.update(part)
    return {"name": Path(path).name, "sha256": digest.hexdigest(), "size_bytes": Path(path).stat().st_size}

def new_detail(source):
    return {"schema": "unity-workmate.renderdoc.detaildata/v1",
            "generated_at": datetime.now(timezone.utc).isoformat(), "source": source, "capture": {},
            "capabilities": {"structured": False, "replay": False, "pipeline": False,
                             "resource_usage": False, "gpu_timing": False},
            "coverage": {}, "overview": {}, "events": [], "draws": [], "stages": [],
            "resources": [], "shaders": [], "images": [], "limitations": []}

def value(node, name, default=None):
    child = next((c for c in node if c.get("name") == name), None)
    if child is None:
        return default
    if child.tag in ("int", "uint", "ResourceId"):
        try:
            return int(child.text or "0")
        except ValueError:
            return default
    return child.get("string", child.text or "")

def estimate_texture(row):
    # 逻辑字节数不包含驱动 padding；不宣称为实际显存。
    bpp = {"GL_RGB8":3, "GL_SRGB8":3, "GL_RGBA8":4, "GL_SRGB8_ALPHA8":4,
           "GL_RGBA4":2, "GL_R8":1, "GL_R32F":4, "GL_DEPTH24_STENCIL8":4}.get(row["format"])
    if bpp is None:
        return {}
    layers = 6 if row["target"] == "GL_TEXTURE_CUBE_MAP" else 1
    size = sum(max(1, row["width"] >> m) * max(1, row["height"] >> m)
               * row["depth"] * layers * bpp for m in range(min(32, row["mips"])))
    return {"size_bytes":size, "size_basis":"logical_estimate_excludes_driver_padding"}

def parse_xml(path, source):
    """chunkIndex 是序列化位置；绝不推算回放 EID。"""
    root = ET.parse(path).getroot()
    d = new_detail(source)
    d["capabilities"]["structured"] = True
    api = root.findtext("header/driver", "Unknown")
    d["capture"]["api"] = api
    labels, textures, stack = {}, {}, []
    active, program, framebuffer = False, None, None
    for c in root.findall("chunks/chunk"):
        call = c.get("name", "")
        if call == "Internal::Driver Initialisation Parameters":
            p = c.find("struct")
            if p is not None:
                d["capture"].update(gpu=value(p,"renderer"), driver=value(p,"version"))
        if call == "glObjectLabel":
            labels[value(c,"Resource",0)] = value(c,"Label","")
        if call in ("glTexStorage2D","glTexStorage3D"):
            rid = value(c,"texture",0)
            textures[rid] = {"id":str(rid), "kind":"texture", "width":value(c,"width"),
                "height":value(c,"height"), "depth":value(c,"depth",1), "mips":value(c,"levels",1),
                "format":value(c,"internalformat"), "target":value(c,"target"),
                "size_bytes":None, "size_basis":"unavailable", "usage":None}
        if call == "Internal::Beginning of Capture":
            active = True
            continue
        if call == "Internal::End of Capture":
            active = False
        if not active:
            continue
        ref = {"event_id":None, "chunk_index":int(c.get("chunkIndex","0"))}
        if call == "glPushDebugGroup":
            stack.append(str(value(c,"message","")))
            d["events"].append(dict(ref, kind="marker", name=stack[-1], path=list(stack)))
        elif call == "glPopDebugGroup":
            if stack: stack.pop()
        elif call == "glUseProgram":
            program = str(value(c,"program",0))
        elif call == "glBindFramebuffer" and value(c,"target") in ("GL_DRAW_FRAMEBUFFER","GL_FRAMEBUFFER"):
            framebuffer = str(value(c,"framebuffer",0))
        elif call == "glViewport":
            d["capture"]["last_viewport"] = {k:value(c,k) for k in ("width","height")}
        elif call.startswith(("glDrawArrays","glDrawElements","glDrawRangeElements","glMultiDraw","glDrawTransformFeedback","glDispatchCompute")):
            mode, count = value(c,"mode"), value(c,"count")
            instances = value(c,"instancecount",value(c,"primcount",1))
            tri = None
            if isinstance(count,int):
                if mode == "GL_TRIANGLES": tri = count // 3 * instances
                elif mode in ("GL_TRIANGLE_STRIP","GL_TRIANGLE_FAN"): tri = max(0,count-2)*instances
            row = dict(ref, kind="dispatch" if call.startswith("glDispatch") else "draw",
                name=call, path=list(stack), count=count, instances=instances, topology=mode,
                triangles=tri, gpu_ms=None, shader_id=program, framebuffer_id=framebuffer,
                pipeline=None, outputs=[], inputs=[])
            d["draws"].append(row)
            d["events"].append({k:row[k] for k in ("event_id","chunk_index","kind","name","path")})
    for rid,t in textures.items():
        t["name"] = labels.get(rid,"")
        t.update(estimate_texture(t))
        d["resources"].append(t)
    used = {r["shader_id"] for r in d["draws"]}
    d["shaders"] = [{"id":str(rid),"name":name} for rid,name in labels.items() if str(rid) in used]
    for r in d["draws"]:
        r["shader_name"] = labels.get(int(r["shader_id"] or 0),"")
    d["coverage"] = {"mode":"structured_only", "draw_api_supported":api in ("OpenGLES","OpenGL"),
                     "event_ids":False, "resources":"partial: immutable GL texture storage only"}
    d["limitations"] = ["基础解析没有回放 EID；chunk_index 不能用于 RenderDoc 事件定位。",
        "基础事件索引仅包含标记、绘制和计算；完整 API 调用仍保留于原始 RDC。",
        "基础解析没有完整管线或资源读写关系，资源清单可能不完整。",
        "没有 GPU 计时；API duration 不能当作 GPU 耗时。"]
    if not d["coverage"]["draw_api_supported"]:
        d["limitations"].append("当前 API 需要成功回放才能统计绘制和资源；这些统计当前不可用。")
    aggregate(d)
    return d

def aggregate(d):
    draws = [r for r in d["draws"] if r["kind"] == "draw"]
    supported = d.get("coverage",{}).get("draw_api_supported",True)
    d["overview"] = {"draw_calls":len(draws) if supported else None,
        "dispatch_calls":sum(r["kind"]=="dispatch" for r in d["draws"]) if supported else None,
        "submitted_triangles":sum(r["triangles"] for r in draws if r.get("triangles") is not None) if supported else None,
        "triangle_draws_unknown":sum(r.get("triangles") is None for r in draws) if supported else None,
        "resource_count":len(d["resources"]), "gpu_ms":None}
    stages = {}
    for r in d["draws"]:
        path = r.get("path") or ["Ungrouped"]
        stage = stages.setdefault(" / ".join(path), {"path":path,"draw_calls":0,"dispatch_calls":0,
            "submitted_triangles":0,"refs":[],"gpu_ms":None})
        stage["draw_calls" if r["kind"]=="draw" else "dispatch_calls"] += 1
        stage["submitted_triangles"] += r.get("triangles") or 0
        stage["refs"].append({"event_id":r["event_id"],"chunk_index":r.get("chunk_index")})
    d["stages"] = list(stages.values())

def build_aidata(d, limit=20):
    shaders = {}
    for r in d["draws"]:
        if r["kind"] != "draw": continue
        name = r.get("shader_name") or r.get("shader_id") or "Unknown"
        entry = shaders.setdefault(name, {"name":name,"draw_calls":0,"example_refs":[]})
        entry["draw_calls"] += 1
        if len(entry["example_refs"]) < 5:
            entry["example_refs"].append({"event_id":r["event_id"],"chunk_index":r.get("chunk_index")})
    resources = sorted(d["resources"],key=lambda r:r.get("size_bytes") or 0,reverse=True)
    draws = sorted((r for r in d["draws"] if r["kind"]=="draw"),key=lambda r:r.get("triangles") or 0,reverse=True)
    return {"schema":"unity-workmate.renderdoc.aidata/v1","generated_at":d["generated_at"],
        "source":d["source"],"capture":d["capture"],"extractor":d.get("extractor",{}),"capabilities":d["capabilities"],
        "coverage":d["coverage"],"overview":d["overview"],
        "stages":[{k:v for k,v in s.items() if k!="refs"} | {"example_refs":s["refs"][:3]} for s in d["stages"]],
        "shader_usage":list(shaders.values()),"largest_resources":[{k:v for k,v in r.items() if k!="usage"} | {"usage_count":len(r["usage"]) if r.get("usage") is not None else None} for r in resources[:limit]],
        "largest_geometry_draws":[{k:r.get(k) for k in ("event_id","chunk_index","name","path",
            "triangles","shader_name","gpu_ms")} for r in draws[:limit]],
        "images":d["images"],"limitations":d["limitations"],
        "selection":{"resource_limit":limit,"geometry_draw_limit":limit,"total_draw_records":len(d["draws"]),
            "all_events_retained_in_detaildata":True,"event_scope":"all extracted action and marker records; original RDC retains full API stream"}, "detail_reference":"capture.detaildata",
        "notes":["确定性数据摘要，不包含模型诊断或根因结论。",
                 "提交三角形数不代表可见像素或 GPU 时间。","纹理逻辑大小不等于实际显存分配。"]}
