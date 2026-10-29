import asyncio
import json

import fastapi
from common import models
from server import main, tools
from server.graph import runner

@main.app.post("/mcp/android/profiler/import", response_model=models.AndroidProfilerResponse)
async def android_profiler_import(request: fastapi.Request, name: str, thread_id: str):
    from server.tools.profiler_capture import import_profiler_raw
    return await import_profiler_raw(request, name, thread_id)


@main.app.post("/chat", response_model = models.ChatResponse)
async def chat(req: models.ChatRequest):
    messages = [m.model_dump() for m in req.messages]
    # 可空：新会话从 greet 开跑
    last_msg = messages[-1] if messages else {"role": "user", "content": ""}
    ai_msg = await runner.req_nostream(
        last_msg,
        req.thread_id,
        agent_role=req.agent_role,
        session_prefs=req.session_prefs.model_dump() if req.session_prefs else None,
    )
    last_msg_obj = ai_msg["messages"][-1]
    usage = getattr(last_msg_obj, "usage_metadata", None) or {}
    return models.ChatResponse(
        content=last_msg_obj.content,
        prompt_tokens=usage.get("input_tokens", 0),
        completion_tokens=usage.get("output_tokens", 0),
    )

@main.app.post("/chat/stream")
async def chat_stream(req: models.ChatRequest):
    messages = [m.model_dump() for m in req.messages]
    last_msg = messages[-1] if messages else {"role": "user", "content": ""}

    async def event_generator():
        prefs = req.session_prefs.model_dump() if req.session_prefs else None
        try:
            async for payload in runner.req_stream(
                last_msg,
                req.thread_id,
                agent_role=req.agent_role,
                session_prefs=prefs,
            ):
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        except Exception as e:
            err = {"type": "error", "content": f"{type(e).__name__}: {e}"}
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'run_end'}, ensure_ascii=False)}\n\n"

    return fastapi.responses.StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
    )


@main.app.get("/chat/gate", response_model=models.ThreadGateResponse)
async def chat_gate(thread_id: str = "default"):
    """打开最近会话：用 checkpoint 恢复 hitl/批准闸门，不靠前端内存。"""
    data = await runner.req_thread_gate(thread_id)
    return models.ThreadGateResponse(**data)


@main.app.get("/health", response_model=models.HealthResponse)
async def health():
    from server.rag.warmup import get_rag_warmup_status

    st = tools.mcp_status
    rag = get_rag_warmup_status()
    return models.HealthResponse(
        ok=True,
        mcp_ok=bool(st.get("ok")),
        mcp_url=str(st.get("url") or ""),
        mcp_tool_count=int(st.get("tool_count") or 0),
        mcp_error=str(st.get("error") or ""),
        rag_ready=bool(rag.get("rag_ready")),
        rag_status=str(rag.get("rag_status") or "pending"),
        rag_detail=str(rag.get("rag_detail") or ""),
    )


@main.app.get("/mcp/android/devices", response_model=models.AndroidDevicesResponse)
async def android_devices():
    """扫描本机 adb 设备，供下拉选择。不启动 Android MCP。"""
    data = await tools.scan_devices()
    return models.AndroidDevicesResponse(**data)


@main.app.get("/mcp/android/status", response_model=models.AndroidPanelResponse)
async def android_status():
    """已连接后的轮询。没连上时 ok=false。"""
    data = await tools.android_panel()
    return models.AndroidPanelResponse(**data)


@main.app.post("/mcp/android/connect", response_model=models.AndroidPanelResponse)
async def android_connect(req: models.AndroidConnectRequest):
    """用户选好设备后点连接，才拉起 Android MCP。"""
    data = await tools.connect_android_panel(req.serial)
    return models.AndroidPanelResponse(**data)


@main.app.post("/mcp/android/disconnect", response_model=models.AndroidPanelResponse)
async def android_disconnect():
    """侧栏点断开：关 Android MCP，不影响 Unity。"""
    data = await tools.disconnect_android_panel()
    return models.AndroidPanelResponse(**data)


@main.app.get("/mcp/android/logcat/stream")
async def android_logcat_stream(serial: str = ""):
    """真机 Logcat SSE：本机 adb，不经 Android MCP。断连即杀进程。"""

    async def event_generator():
        try:
            async for frame in tools.iter_logcat_sse(serial):
                yield frame
        except asyncio.CancelledError:
            raise
        except Exception as e:
            err = {"type": "error", "content": f"{type(e).__name__}: {e}"}
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"

    return fastapi.responses.StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@main.app.post("/mcp/android/logcat/clear", response_model=models.AndroidLogcatClearResponse)
async def android_logcat_clear(req: models.AndroidLogcatClearRequest):
    """清除设备 logcat 缓冲区。"""
    data = await tools.clear_logcat(req.serial)
    return models.AndroidLogcatClearResponse(**data)


@main.app.post("/mcp/android/perfetto/capture", response_model=models.AndroidPerfettoResponse)
async def android_perfetto_capture(req: models.AndroidPerfettoCaptureRequest):
    """真机 Perfetto 采样（不进对话）。"""
    data = await tools.capture_perfetto(
        duration_sec=req.duration_sec,
        serial=req.serial,
        package=req.package,
        thread_id=req.thread_id,
    )
    return models.AndroidPerfettoResponse(**data)


@main.app.get("/mcp/android/perfetto/latest", response_model=models.AndroidPerfettoResponse)
async def android_perfetto_latest(thread_id: str = ""):
    """最近一次 Perfetto 结果。"""
    return models.AndroidPerfettoResponse(**tools.latest_perfetto(thread_id))


@main.app.get("/mcp/android/perfetto/history", response_model=models.AndroidPerfettoHistoryResponse)
async def android_perfetto_history(limit: int = 40, thread_id: str = ""):
    """Perfetto trace 历史列表。"""
    data = tools.list_perfetto_history(limit=limit, thread_id=thread_id)
    return models.AndroidPerfettoHistoryResponse(**data)


@main.app.post("/mcp/android/perfetto/load", response_model=models.AndroidPerfettoResponse)
async def android_perfetto_load(req: models.AndroidPerfettoLoadRequest):
    """打开一条历史 trace。"""
    data = await tools.load_perfetto_history(req.id, thread_id=req.thread_id)
    return models.AndroidPerfettoResponse(**data)


@main.app.get("/mcp/android/perfetto/download")
async def android_perfetto_download(name: str = ""):
    """下载 .perfetto-trace。"""
    path = tools.latest_trace_file(name)
    if path is None:
        raise fastapi.HTTPException(status_code=404, detail="还没有可下载的 trace")
    return fastapi.responses.FileResponse(
        path,
        filename=path.name,
        media_type="application/octet-stream",
    )


@main.app.post("/mcp/android/profiler/start", response_model=models.AndroidProfilerResponse)
async def android_profiler_start(req: models.AndroidProfilerStartRequest):
    """通知 kabu-profiler 开始写 .raw（不冷启）。"""
    data = await tools.start_profiler_raw(
        serial=req.serial,
        package=req.package,
        thread_id=req.thread_id,
    )
    return models.AndroidProfilerResponse(**data)


@main.app.post("/mcp/android/profiler/stop", response_model=models.AndroidProfilerResponse)
async def android_profiler_stop():
    """通知插件停录，并 pull .raw 与截图。"""
    data = await tools.stop_profiler_raw()
    return models.AndroidProfilerResponse(**data)


@main.app.get("/mcp/android/profiler/latest", response_model=models.AndroidProfilerResponse)
async def android_profiler_latest(thread_id: str = ""):
    """最近一次 Profiler .raw 录制状态。"""
    return models.AndroidProfilerResponse(**tools.latest_profiler(thread_id))


@main.app.get("/mcp/android/profiler/history", response_model=models.AndroidProfilerHistoryResponse)
async def android_profiler_history(limit: int = 40, thread_id: str = ""):
    """Profiler .raw 历史列表。"""
    data = await asyncio.to_thread(
        tools.list_profiler_history,
        limit,
        thread_id,
    )
    return models.AndroidProfilerHistoryResponse(**data)


@main.app.post("/mcp/android/profiler/load", response_model=models.AndroidProfilerResponse)
async def android_profiler_load(req: models.AndroidProfilerLoadRequest):
    """打开一条历史 .raw（sidecar 或轻量窥视）。

    窥视大 .raw 会读数 MB，必须进线程，避免卡住整站（历史列表也会被拖成超时）。
    """
    data = await asyncio.to_thread(
        tools.load_profiler_history,
        req.id,
        req.thread_id,
    )
    return models.AndroidProfilerResponse(**data)


@main.app.post(
    "/mcp/android/profiler/open-editor",
    response_model=models.AndroidProfilerOpenResponse,
)
async def android_profiler_open_editor(req: models.AndroidProfilerOpenRequest):
    """按 .raw 内嵌版本匹配本机 Hub Editor，拉起 Profiler 并 Load。"""
    data = await asyncio.to_thread(
        tools.open_profiler_in_editor,
        req.id,
        req.thread_id,
        req.frame,
    )
    return models.AndroidProfilerOpenResponse(**data)


@main.app.post(
    "/mcp/android/profiler/reveal",
    response_model=models.AndroidProfilerRevealResponse,
)
async def android_profiler_reveal(req: models.AndroidProfilerRevealRequest):
    """资源管理器打开这条录制所在的 profiler 目录。"""
    data = await asyncio.to_thread(
        tools.reveal_profiler_data,
        req.id,
        req.thread_id,
    )
    return models.AndroidProfilerRevealResponse(**data)


@main.app.post(
    "/mcp/android/profiler/convert",
    response_model=models.AndroidProfilerConvertResponse,
)
async def android_profiler_convert(req: models.AndroidProfilerConvertRequest):
    """一键 .raw → .data + .detaildata（旁路 Editor），再裁 .aidata。"""
    data = await asyncio.to_thread(
        tools.convert_profiler_analysis,
        req.id,
        req.thread_id,
        req.timeout_sec,
    )
    return models.AndroidProfilerConvertResponse(**data)


@main.app.get("/mcp/android/profiler/calltree")
async def android_profiler_calltree(name: str, frame: int, thread_id: str = ""):
    """单帧调用树按需下载，不进入 latest 轮询和模型摘要。"""
    path = tools.profiler_calltree_file(name, thread_id, frame)
    if path is None:
        raise fastapi.HTTPException(status_code=404, detail="暂无该帧调用树，请重新生成分析数据")
    return fastapi.responses.FileResponse(path, media_type="application/json")


@main.app.get("/mcp/android/profiler/detaildata")
async def android_profiler_detaildata(name: str = "", thread_id: str = ""):
    """详情面板读的全帧 JSON。不塞进 latest，避免轮询拖着几十 MB。"""
    path = tools.profiler_detaildata_file(name, thread_id)
    if path is None:
        raise fastapi.HTTPException(status_code=404, detail="还没有 .detaildata")
    return fastapi.responses.FileResponse(
        path,
        filename=path.name,
        media_type="application/json",
    )


@main.app.get("/mcp/android/profiler/shot")
async def android_profiler_shot(name: str = "", file: str = ""):
    """本会话 .raw 旁的截图。file 只能是文件名。"""
    path = tools.profiler_shot_file(name, file)
    if path is None:
        raise fastapi.HTTPException(status_code=404, detail="没有这张截图")
    return fastapi.responses.FileResponse(path, filename=path.name, media_type="image/jpeg")


@main.app.get("/mcp/android/profiler/download")
async def android_profiler_download(name: str = ""):
    """下载 .raw；name 可指定历史文件名。"""
    path = tools.latest_profiler_file(name)
    if path is None:
        raise fastapi.HTTPException(status_code=404, detail="还没有可下载的 .raw")
    return fastapi.responses.FileResponse(
        path,
        filename=path.name,
        media_type="application/octet-stream",
    )


@main.app.post("/mcp/reconnect", response_model=models.McpReconnectResponse)
async def mcp_reconnect(req: models.McpReconnectRequest):
    """连接页改 MCP URL：热重连并默认写回 mcp.json。"""
    try:
        st = await tools.reconnect_mcp(req.url, persist=req.persist)
        return models.McpReconnectResponse(
            ok=True,
            mcp_ok=bool(st.get("ok")),
            mcp_url=str(st.get("url") or ""),
            mcp_tool_count=int(st.get("tool_count") or 0),
            mcp_error=str(st.get("error") or ""),
        )
    except Exception as e:
        st = tools.mcp_status
        return models.McpReconnectResponse(
            ok=False,
            mcp_ok=False,
            mcp_url=str(st.get("url") or ""),
            mcp_tool_count=int(st.get("tool_count") or 0),
            mcp_error=f"{type(e).__name__}: {e}",
        )


@main.app.post("/chat/resume")
async def chat_resume(req: models.ResumeRequest):
    async def event_generator():
        try:
            async for payload in runner.req_resume_stream(
                thread_id=req.thread_id,
                action=req.action,
                step_index=req.step_index,
                content=req.content or "",
            ):
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        except Exception as e:
            err = {"type": "error", "content": f"{type(e).__name__}: {e}"}
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'run_end'}, ensure_ascii=False)}\n\n"

    return fastapi.responses.StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
    )


@main.app.post("/mcp/android/profiler/custom-frames")
async def android_profiler_custom_frames(req: models.AndroidProfilerFramesRequest):
    from server.tools.profiler_capture import select_profiler_frames
    try:
        return await asyncio.to_thread(select_profiler_frames, req.id, req.thread_id, req.frames)
    except ValueError as exc:
        raise fastapi.HTTPException(status_code=400, detail=str(exc)) from exc
