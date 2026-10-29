"""RenderDoc HTTP 接口；不依赖对话或 Android MCP 连接。"""
import asyncio
import re
import shutil
from pathlib import Path
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from server.tools import renderdoc_capture as capture

router = APIRouter(prefix="/mcp/renderdoc", tags=["renderdoc"])
MAX_UPLOAD = 2 * 1024**3

class ImportRequest(BaseModel):
    path: str = Field(min_length=1,max_length=4096)
    remote_host: str = Field(default="",max_length=255)

def check_host(host):
    if host and not re.fullmatch(r"[A-Za-z0-9_.:\-]+",host):
        raise HTTPException(400,"远端地址格式无效")
    return host

@router.get("/environment")
async def environment():
    return capture.locate_tools()

@router.get("/history")
async def history():
    return {"items":capture.history()}

@router.post("/import")
async def import_capture(body: ImportRequest):
    path = Path(body.path).expanduser()
    if path.suffix.lower() != ".rdc" or not path.is_file():
        raise HTTPException(400,"请选择存在的 .rdc 文件")
    host = check_host(body.remote_host)
    job = capture.create_job(path.name,host)
    try:
        await asyncio.to_thread(shutil.copyfile,path,capture.directory(job["id"])/"capture.rdc")
        return capture.enqueue(job["id"])
    except Exception as exc:
        capture.update(job["id"],status="error",error=str(exc))
        raise HTTPException(400,"导入失败，请检查文件是否可读") from None

@router.post("/upload")
async def upload(request: Request, name: str, remote_host: str = ""):
    name = Path(name.replace("\\","/")).name
    if not name.lower().endswith(".rdc"):
        raise HTTPException(400,"仅支持 .rdc 文件")
    check_host(remote_host)
    job = capture.create_job(name,remote_host)
    try:
        total = 0
        with (capture.directory(job["id"])/"capture.rdc").open("wb") as output:
            async for chunk in request.stream():
                total += len(chunk)
                if total > MAX_UPLOAD: raise HTTPException(413,"文件超过 2 GiB")
                await asyncio.to_thread(output.write,chunk)
        if not total: raise HTTPException(400,"文件为空")
        return capture.enqueue(job["id"])
    except BaseException:
        capture.update(job["id"],status="error",error="上传未完成，请重新导入")
        raise

@router.get("/{job_id}")
async def status(job_id: str):
    try: return capture.load_job(job_id)
    except (ValueError,FileNotFoundError): raise HTTPException(404,"抓帧不存在") from None

@router.get("/{job_id}/data")
async def data(job_id: str, kind: str = "aidata"):
    if kind not in ("aidata","detaildata"): raise HTTPException(400,"不支持的数据类型")
    try: return capture.read_data(job_id,kind)
    except (ValueError,FileNotFoundError): raise HTTPException(404,"数据尚未生成") from None

@router.get("/{job_id}/files/{name}")
async def download(job_id: str, name: str):
    try: path = capture.file_path(job_id,name)
    except (ValueError,FileNotFoundError): raise HTTPException(404,"文件不存在") from None
    return FileResponse(path,filename=path.name)
