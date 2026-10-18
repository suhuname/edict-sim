"""HTTP 面 —— 颁旨、看板、SSE 实时流、产出预览。"""
from __future__ import annotations

import asyncio
import json
import queue
from pathlib import Path
from typing import Any

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import orchestrator
from . import task as taskmod
from .config import ROOT, get_settings
from .hub import hub
from .kernel import task_root, write_instruction
from .roles import ROLE_ORDER, MINISTRIES, ROLES
from .web import web

s = get_settings()


@asynccontextmanager
async def lifespan(_app):  # noqa: ANN001
    # 联网网关后台预热：连上之前朝会照跑，只是没有联网工具
    web.start(blocking=False)
    yield


app = FastAPI(title="三省六部 · 朝堂信息流", version="1.0.0", lifespan=lifespan)

_web = s.web_dir
app.mount("/static", StaticFiles(directory=str(_web)), name="static")


class Decree(BaseModel):
    title: str
    detail: str = ""


class Followup(BaseModel):
    title: str = ""
    detail: str = ""


class ExportReq(BaseModel):
    dest: str | None = None


_FORBIDDEN_DEST = ("c:\\windows", "c:\\program files", "c:\\program files (x86)",
                   "c:\\programdata", "\\system32")

_BAD_NAME = '\\/:*?"<>|'


def _folder_name(t) -> str:
    """导出目录名：旨意编号 + 颁旨原文（去掉 Windows 非法字符，限长）。"""
    title = "".join("_" if c in _BAD_NAME or c in "\r\n\t" else c for c in (t.title or ""))
    title = " ".join(title.split()).strip(". " )[:60]
    return f"{t.id} {title}".strip()


@app.post("/api/tasks/{task_id}/followup")
async def followup(task_id: str, body: Followup) -> dict[str, Any]:
    """回奏：不新开旨意，在原旨意上续一轮，从太子重新走三省六部。"""
    t = taskmod.get(task_id)
    if not t:
        raise HTTPException(404, "无此旨意")
    if t.status == "running":
        raise HTTPException(400, "这道旨意还在办，等它回奏后再续")
    if t.state not in taskmod.REOPEN_FROM:
        raise HTTPException(400, f"当前状态 {t.state} 还不能回奏（需先走完一轮）")

    title = (body.title or "").strip() or f"对《{t.title}》的补充"
    info = t.reopen(title, (body.detail or "").strip())
    taskmod.save()
    hub.emit(t.id, "followup", round=info["round"], title=title,
             detail=(body.detail or "")[:600], from_state=info["from"])
    orchestrator.start(t)
    return {"ok": True, "task_id": t.id, "round": info["round"], "state": t.state}


@app.get("/api/export/default")
async def export_default() -> dict[str, Any]:
    return {"dest": str(ROOT / "doc")}


@app.post("/api/export/{task_id}")
async def export_task(task_id: str, req: ExportReq) -> dict[str, Any]:
    """把沙箱里的产出整体导出到用户指定的本地目录（默认项目下 doc/）。"""
    import shutil

    from .config import ROOT

    t = taskmod.get(task_id)
    if not t:
        raise HTTPException(404, "无此旨意")
    raw = (req.dest or "").strip() or str(Path(ROOT) / "doc")
    dest = Path(raw).expanduser()
    if not dest.is_absolute():
        raise HTTPException(400, "请填写绝对路径，例如 E:\\我的文档\\朝会产出")
    low = str(dest).lower()
    if any(bad in low for bad in _FORBIDDEN_DEST):
        raise HTTPException(400, "不允许写入系统目录")

    src = task_root(task_id)
    out = dest / _folder_name(t)
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, out, dirs_exist_ok=True)
    except OSError as exc:
        raise HTTPException(400, f"写入失败：{exc}") from exc

    files = [p for p in out.rglob("*") if p.is_file()]
    hub.emit(task_id, "audit", role="system", label="内侍", action="export", ok=True,
             detail=f"导出 {len(files)} 份文书 → {out}")
    return {"ok": True, "dest": str(out), "files": len(files),
            "bytes": sum(p.stat().st_size for p in files)}



@app.get("/")
async def index() -> FileResponse:
    return FileResponse(str(_web / "index.html"))


@app.get("/api/health")
async def health() -> dict[str, Any]:
    import os

    return {
        "ok": True,
        "model": s.model,
        "key_present": bool(os.environ.get(s.api_key_env)),
        "sandbox": str(s.sandbox_root),
        "budget": {"llm_calls": s.max_llm_calls, "rounds": s.max_rounds, "depth": s.max_depth},
        "web": web.status(),
    }


@app.get("/api/web/status")
async def web_status() -> dict[str, Any]:
    return web.status()


@app.get("/api/roles")
async def roles() -> dict[str, Any]:
    return {
        "roles": [
            {
                "id": r.id, "label": r.label, "name": r.name, "emoji": r.emoji,
                "layer": r.layer, "duty": r.duty, "allows": r.allows,
                "allows_label": [ROLES[a].label for a in r.allows],
                "dept_dir": r.dept_dir, "tone": r.tone,
                "rank": ROLE_ORDER.index(r.id) if r.id in ROLE_ORDER else 99,
                "can_state": "kanban_state" in r.policy,
                "web": bool(r.web),
            }
            for r in ROLES.values()
        ],
        "ministries": MINISTRIES,
    }


@app.post("/api/decree")
async def decree(d: Decree) -> dict[str, Any]:
    title = (d.title or "").strip()
    if not title:
        raise HTTPException(400, "旨意不能为空")
    t = taskmod.new_task(title, (d.detail or "").strip())
    orchestrator.start(t)
    return {"task_id": t.id}


@app.get("/api/tasks")
async def tasks() -> dict[str, Any]:
    return {
        "tasks": [
            {k: v for k, v in t.to_dict().items()
             if k not in ("audit", "progress_log", "instructions")}
            for t in taskmod.all_tasks()
        ]
    }


@app.get("/api/tasks/{task_id}")
async def task_detail(task_id: str) -> dict[str, Any]:
    t = taskmod.get(task_id)
    if not t:
        raise HTTPException(404, "无此旨意")
    # 老旨意没有原文记录 / 没落盘：这里补一次，保证弹窗和总线都能点开
    if not any(int(i.get("round") or 0) == 1 for i in t.instructions):
        t.instructions.insert(0, {"round": 1, "kind": "decree", "title": t.title,
                                  "detail": t.detail, "at": t.created_at, "md": ""})
    for ins in t.instructions:
        rnd = int(ins.get("round") or 1)
        ins.setdefault("kind", "decree" if rnd == 1 else "followup")
        expect = (f"圣旨/圣旨原文·第{rnd}轮.md" if ins["kind"] == "decree"
                  else f"圣旨/回奏·第{rnd}轮.md")
        if ins.get("md") != expect:
            try:
                write_instruction(t, rnd, ins["kind"], ins.get("title", ""), ins.get("detail", ""))
            except OSError:
                pass
    data = t.to_dict()
    data["files_detail"] = _scan_files(task_id)
    return data


def _scan_files(task_id: str) -> list[dict[str, Any]]:
    root = task_root(task_id)
    out = []
    for p in sorted(root.rglob("*")):
        if p.is_file():
            try:
                out.append({"path": p.relative_to(root).as_posix(), "size": p.stat().st_size,
                            "mtime": int(p.stat().st_mtime)})
            except OSError:
                pass
    return out


@app.get("/api/artifact/{task_id}")
async def artifact(task_id: str, path: str) -> dict[str, Any]:
    root = task_root(task_id)
    target = (root / path).resolve()
    if target != root and root not in target.parents:
        raise HTTPException(403, "越界")
    if not target.is_file():
        raise HTTPException(404, "无此文书")
    text = target.read_text(encoding="utf-8", errors="replace")
    return {"path": path, "size": len(text), "text": text[:80_000]}


@app.delete("/api/tasks/{task_id}")
async def delete_task(task_id: str, purge: bool = True) -> dict[str, Any]:
    """删除一道旨意：看板记录、总线回放、（可选）沙箱目录。"""
    import shutil

    t = taskmod.get(task_id)
    if not t:
        raise HTTPException(404, "无此旨意")
    if t.status == "running":
        raise HTTPException(400, "这道旨意还在办，请先「叫停」再删")

    files = len(t.files)
    record = taskmod.delete_task(task_id)
    hub.drop(task_id)
    if purge:
        shutil.rmtree(s.sandbox_root / task_id, ignore_errors=True)
    return {"ok": True, "task": record, "purged": bool(purge), "files": files}


@app.post("/api/abort/{task_id}")
async def abort(task_id: str) -> dict[str, Any]:
    orchestrator.abort(task_id)
    return {"ok": True}


@app.get("/api/stream/{task_id}")
async def stream(task_id: str) -> StreamingResponse:
    if not taskmod.get(task_id):
        raise HTTPException(404, "无此旨意")

    async def gen():  # noqa: ANN202
        q = hub.subscribe(task_id)
        last_seq = 0
        idle = 0.0
        try:
            # 先回放历史，再接实时（按 seq 去重）
            for ev in hub.history(task_id):
                last_seq = max(last_seq, int(ev.get("seq") or 0))
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            t = taskmod.get(task_id)
            if t and t.finished:
                yield "data: " + json.dumps({"kind": "stream_end", "seq": last_seq},
                                            ensure_ascii=False) + "\n\n"
                return
            while True:
                try:
                    ev = await asyncio.to_thread(q.get, True, 1.0)
                except queue.Empty:
                    idle += 1
                    yield ": ping\n\n"
                    t = taskmod.get(task_id)
                    if t and t.finished:
                        yield "data: " + json.dumps({"kind": "stream_end", "seq": last_seq},
                                                    ensure_ascii=False) + "\n\n"
                        return
                    continue
                seq = int(ev.get("seq") or 0)
                if seq <= last_seq:
                    continue
                last_seq = seq
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                if ev.get("kind") == "court_end":
                    yield "data: " + json.dumps({"kind": "stream_end", "seq": last_seq},
                                                ensure_ascii=False) + "\n\n"
                    return
        finally:
            hub.unsubscribe(task_id, q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                                      "Connection": "keep-alive"})


@app.exception_handler(HTTPException)
async def http_exc(_req, exc: HTTPException) -> JSONResponse:  # noqa: ANN001
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
