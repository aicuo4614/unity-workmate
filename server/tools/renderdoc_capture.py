"""RDC 导入、隔离回放进程、后台转换及历史；独立于对话图。"""
from __future__ import annotations
import asyncio
import json
import os
import re
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from server.tools.renderdoc_data import aggregate, build_aidata, new_detail, parse_xml, source_info, write_json

ROOT = Path(__file__).resolve().parents[1] / "logs" / "renderdoc"
_TASKS: dict[str, asyncio.Task] = {}
_GATE = asyncio.Semaphore(1)
ID_RE = re.compile(r"^[a-f0-9]{32}$")
TRANSIENT = {"queued", "opening", "replaying", "extracting", "summarizing"}
ALLOWED_FILES = {"capture.rdc", "capture.detaildata", "capture.aidata", "thumbnail.png", "last-output.png"}

def locate_tools():
    # 可配置路径优先，随后 PATH 和常见官方安装目录。
    configured = os.environ.get("RENDERDOC_DIR","")
    roots = [Path(configured)] if configured else []
    cmd = shutil.which("renderdoccmd")
    if cmd: roots.append(Path(cmd).parent)
    roots += [Path(os.environ.get("ProgramFiles",r"C:\Program Files"))/"RenderDoc", Path(r"D:\Work\RenderDoc")]
    root = next((p for p in roots if (p/"renderdoccmd.exe").is_file() or (p/"renderdoccmd").is_file()),None)
    if root is None:
        return {"available":False,"error":"未找到 RenderDoc；请设置服务端 RENDERDOC_DIR 后重启。"}
    suffix = ".exe" if os.name == "nt" else ""
    return {"available":True,"directory":str(root),
            "command":str(root/("renderdoccmd"+suffix)), "replay_command":str(root/("qrenderdoc"+suffix)),
            "embedded_python":(root/("qrenderdoc"+suffix)).is_file()}

def directory(job_id):
    if not ID_RE.fullmatch(job_id): raise ValueError("无效的抓帧 ID")
    return ROOT/job_id

def load_job(job_id):
    path = directory(job_id)/"job.json"
    if not path.is_file(): raise FileNotFoundError("抓帧不存在")
    job = json.loads(path.read_text(encoding="utf-8-sig"))
    if job["status"] in TRANSIENT and job_id not in _TASKS:
        job.update(status="interrupted", error="服务已重启，转换被中断；可重新转换原始文件。")
    return job

def history():
    if not ROOT.exists(): return []
    jobs = []
    for path in ROOT.glob("*/job.json"):
        try: jobs.append(load_job(path.parent.name))
        except (ValueError,OSError): pass
    return sorted(jobs,key=lambda j:j["created_at"],reverse=True)[:100]

def create_job(name, remote_host=""):
    job_id = uuid.uuid4().hex
    folder = directory(job_id)
    folder.mkdir(parents=True)
    job = {"id":job_id,"name":Path(name).name,"status":"importing","error":"",
           "created_at":datetime.now(timezone.utc).isoformat(),"size_bytes":0,
           "remote_host":remote_host,"overview":None,"capabilities":None,"warnings":[]}
    write_json(folder/"job.json",job)
    return job

def update(job_id, **changes):
    path = directory(job_id)/"job.json"
    job = json.loads(path.read_text(encoding="utf-8-sig"))
    job.update(changes)
    write_json(path,job)
    return job

def run_command(args, timeout, env=None):
    kwargs = {"creationflags":subprocess.CREATE_NO_WINDOW} if os.name=="nt" else {}
    result = subprocess.run(args, capture_output=True, timeout=timeout, env=env, **kwargs)
    if result.returncode:
        message = (result.stderr or result.stdout).decode("utf-8","replace")[-2000:]
        raise RuntimeError(message or "RenderDoc 进程失败")
    return result

def convert(job_id):
    folder, tools = directory(job_id), locate_tools()
    if not tools["available"]: raise RuntimeError(tools["error"])
    version = run_command([tools["command"], "version"], timeout=15).stdout.decode("utf-8", "replace").strip()
    source = source_info(folder/"capture.rdc")
    source["name"] = load_job(job_id)["name"]
    request = {"path":str(folder/"capture.rdc"),"directory":str(folder),
               "remote_host":load_job(job_id)["remote_host"]}
    write_json(folder/"request.json",request)
    warnings, replay = [], None
    update(job_id,status="replaying")
    if tools.get("embedded_python"):
        env = dict(os.environ,WORKMATE_RENDERDOC_REQUEST=str(folder/"request.json"))
        try:
            run_command([tools["replay_command"],"--python",str(Path(__file__).with_name("renderdoc_worker.py"))],
                        timeout=300,env=env)
            replay = json.loads((folder/"replay-result.json").read_text(encoding="utf-8-sig"))
            if not replay.get("ok"): warnings.append("完整回放不可用：" + replay.get("error","未知错误"))
        except (OSError,ValueError,subprocess.SubprocessError,RuntimeError) as exc:
            warnings.append("完整回放不可用：" + str(exc))
    else:
        warnings.append("未找到 qrenderdoc 内置 Python，使用基础结构解析。")
    update(job_id,status="extracting")
    if replay and replay.get("ok"):
        detail = new_detail(source)
        for key in ("capture","events","draws","resources","shaders","images","limitations","coverage"):
            detail[key] = replay[key]
        detail["capabilities"].update(replay=True, pipeline=not replay["coverage"].get("pipeline_failures"),
            resource_usage=all(r["usage"] is not None for r in detail["resources"]))
        aggregate(detail)
    else:
        xml = folder/"capture.xml"
        run_command([tools["command"],"convert","-f",str(folder/"capture.rdc"),"-o",str(xml),"-c","xml"],timeout=600)
        detail = parse_xml(xml,source)
    # 即使回放失败，仍尽量保留抓帧缩略图；绝不把缩略图说成逐事件回放结果。
    if not (folder/"thumbnail.png").is_file():
        try:
            run_command([tools["command"],"thumb","--out",str(folder/"thumbnail.png"),str(folder/"capture.rdc")],timeout=60)
        except (OSError,subprocess.SubprocessError,RuntimeError) as exc:
            warnings.append("缩略图不可用：" + str(exc))
    if (folder/"thumbnail.png").is_file() and not any(i["file"]=="thumbnail.png" for i in detail["images"]):
        detail["images"].append({"file":"thumbnail.png","kind":"embedded_thumbnail","event_id":None})
    detail["limitations"].extend(warnings)
    detail["extractor"] = {"backend":"qrenderdoc_python" if detail["capabilities"]["replay"] else "renderdoccmd_structured",
                           "tool_directory":tools["directory"], "renderdoc_version":version}
    update(job_id,status="summarizing")
    write_json(folder/"capture.detaildata",detail)
    aidata = build_aidata(detail)
    write_json(folder/"capture.aidata",aidata)
    update(job_id,status="done" if detail["capabilities"]["replay"] else "partial",
           size_bytes=source["size_bytes"],source=source,overview=detail["overview"],
           capabilities=detail["capabilities"],warnings=detail["limitations"],
           finished_at=datetime.now(timezone.utc).isoformat())

async def _convert(job_id):
    try:
        async with _GATE:
            await asyncio.to_thread(convert,job_id)
    except Exception as exc:
        update(job_id,status="error",error=str(exc))
    finally:
        _TASKS.pop(job_id,None)

def enqueue(job_id):
    update(job_id,status="queued")
    _TASKS[job_id] = asyncio.create_task(_convert(job_id))
    return load_job(job_id)

def read_data(job_id, kind):
    path = file_path(job_id, "capture."+kind)
    return json.loads(path.read_text(encoding="utf-8-sig"))

def file_path(job_id, name):
    if name not in ALLOWED_FILES: raise ValueError("文件类型不允许")
    path = directory(job_id)/name
    if not path.is_file(): raise FileNotFoundError("文件尚未生成")
    return path
