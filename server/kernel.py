"""神机内核 —— 把 agent-core 的回合循环接到朝堂上。

复用（不复制）用户自己的可嵌入内核 E:\\Project\\agent-core：
    AgentLoop（finish_reason 判据）/ ToolRegistry（工具契约）/ LoopGuard（防空转）
    IterationBudget（预算）/ EventBus（事件流）/ OpenAICompatProvider（流式 DeepSeek）

本文件只做三件事：
    1. 给每个角色装配工具（沙箱读写 + 看板 + 差遣）
    2. 把内核事件翻译成朝会事件（hub）
    3. 保证越权/越界/非法跳转**当场被拒**并留下审计
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from typing import Any

from . import task as taskmod
from .config import get_settings
from .hub import StreamThrottle, hub
from .roles import ROLES, Role, runtime_brief
from .web import web

# ── 接入 agent-core（配置里的 kernel.dir）─────────────────────────────────
_S = get_settings()
if str(_S.kernel_dir) not in sys.path:
    sys.path.insert(0, str(_S.kernel_dir))

from agent import events as ev  # noqa: E402
from agent.budget import IterationBudget  # noqa: E402
from agent.guard import LoopGuard  # noqa: E402
from agent.loop import AgentLoop  # noqa: E402
from agent.provider.deepseek import OpenAICompatProvider  # noqa: E402
from agent.tools import ToolRegistry  # noqa: E402


class BudgetExceeded(RuntimeError):
    pass


_provider: OpenAICompatProvider | None = None
_provider_lock = threading.Lock()


def provider() -> OpenAICompatProvider:
    global _provider
    with _provider_lock:
        if _provider is None:
            s = get_settings()
            _provider = OpenAICompatProvider(
                base_url=s.base_url, model=s.model, api_key_env=s.api_key_env,
                timeout_s=s.timeout_s, temperature=s.temperature,
            )
        return _provider


# ── 沙箱 ─────────────────────────────────────────────────────────────────

def task_root(task_id: str) -> Path:
    root = (get_settings().sandbox_root / task_id).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _resolve(root: Path, raw: str) -> Path:
    """把模型给的相对路径解析到 root 内；越界抛错（这是沙箱的物理边界）。"""
    p = Path(str(raw or ".").replace("\\", "/"))
    cand = (root / p) if not p.is_absolute() else p
    cand = cand.resolve()
    if cand != root and root not in cand.parents:
        raise ValueError(f"越界：{raw!r} 落在沙箱 ({root}) 之外，恕难从命")
    return cand


def _write_dir(task_id: str, r: Role) -> Path:
    d = (task_root(task_id) / r.dept_dir).resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


INSTRUCTION_DIR = "圣旨"


def write_instruction(task, rnd: int, kind: str, title: str, detail: str) -> str:
    """把本轮圣旨/回奏原文落成 md（放在 圣旨/ 下），并登记到 files。"""
    root = task_root(task.id)
    d = root / INSTRUCTION_DIR
    d.mkdir(parents=True, exist_ok=True)
    name = f"圣旨原文·第{rnd}轮.md" if kind == "decree" else f"回奏·第{rnd}轮.md"
    target = d / name
    head = "圣旨" if kind == "decree" else "回奏"
    stamp = taskmod.now_iso()
    body = (
        f"# {head} · 第 {rnd} 轮\n\n"
        f"- 旨意编号：{task.id}\n"
        f"- 下旨时间：{stamp}\n"
        f"- 标题：{title or '（无）'}\n\n"
        f"## 原文\n\n{detail or '（无详述）'}\n"
    )
    target.write_text(body, encoding="utf-8")
    other = d / (f"回奏·第{rnd}轮.md" if kind == "decree" else f"圣旨原文·第{rnd}轮.md")
    if other != target:
        stale = f"{INSTRUCTION_DIR}/{other.name}"
        if other.is_file():
            try:
                other.unlink()
            except OSError:
                pass
        if stale in task.files:          # 清掉错名的登记，别在看板留幽灵条目
            task.files.remove(stale)
    rel = f"{INSTRUCTION_DIR}/{name}"
    if rel not in task.files:
        task.files.append(rel)
    for ins in task.instructions:
        if ins.get("round") == rnd:
            ins["md"] = rel
            break
    taskmod.save()
    hub.emit(task.id, "artifact", role="system", label="内侍", path=rel,
             size=len(body), round=rnd)
    return rel


def _rel(task_id: str, p: Path) -> str:
    try:
        return p.relative_to(task_root(task_id)).as_posix()
    except ValueError:
        return str(p)


# ── 工具装配 ─────────────────────────────────────────────────────────────

def _audit(task, r: Role, action: str, ok: bool, detail: str = "") -> None:
    task.log(r.label, action, ok=ok, detail=detail)
    hub.emit(task.id, "audit", role=r.id, label=r.label, action=action, ok=ok, detail=detail[:300])


def _deny(task, r: Role, tool: str, reason: str) -> str:
    _audit(task, r, tool, False, reason)
    return f"⛔ {reason}。此令已驳回并记入审计，勿再试。"


def _build_tools(task, r: Role, depth: int) -> ToolRegistry:
    s = get_settings()
    reg = ToolRegistry(pool_workers=4)
    root = task_root(task.id)

    # ---------- 1. 只读 ----------

    def list_dir(args: dict) -> str:
        target = _resolve(root, args.get("path") or ".")
        if not target.is_dir():
            return f"不是目录：{_rel(task.id, target)}"
        entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name))
        if not entries:
            return f"{_rel(task.id, target)}/ 空"
        lines = [f"{'📁' if e.is_dir() else '📄'} {_rel(task.id, e)}" for e in entries[:100]]
        return "本旨意目录：\n" + "\n".join(lines)

    def read_file(args: dict) -> str:
        target = _resolve(root, args.get("path") or "")
        if not target.is_file():
            return f"无此文件：{args.get('path')}（可先用 list_dir . 看有什么）"
        data = target.read_text(encoding="utf-8", errors="replace")
        if len(data) > s.max_read_bytes:
            data = data[: s.max_read_bytes] + f"\n…（已截断，原 {len(data)} 字）"
        return data

    # ---------- 2. 写（部门边界）----------

    def write_file(args: dict) -> str:
        rel = str(args.get("path") or "").strip()
        content = args.get("content")
        if not rel:
            return "缺 path"
        if content is None:
            return "缺 content"
        content = str(content)
        if len(content.encode("utf-8")) > s.max_write_bytes:
            return _deny(task, r, "write_file", f"文书过大（>{s.max_write_bytes // 1000}KB）")
        try:
            target = _resolve(root, rel)
        except ValueError as exc:
            return _deny(task, r, "write_file", str(exc))
        allowed = _write_dir(task.id, r)
        if s.dept_write_only and target != allowed and allowed not in target.parents:
            return _deny(task, r, "write_file", f"{r.label} 只能写 {r.dept_dir}/，你写的是 {_rel(task.id, target)}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        relpath = _rel(task.id, target)
        if relpath not in task.files:
            task.files.append(relpath)
        taskmod.save()
        hub.emit(task.id, "artifact", role=r.id, label=r.label, path=relpath, size=len(content))
        return f"已落笔：{relpath}（{len(content)} 字）"

    # ---------- 3. 看板 ----------

    def _need(perm: str) -> str | None:
        if perm not in r.policy:
            return _deny(task, r, perm, f"{r.label} 无权执行 {perm}")
        return None

    def kanban_state(args: dict) -> str:
        if (msg := _need("kanban_state")):
            return msg
        new = str(args.get("state") or args.get("new_state") or "").strip()
        remark = str(args.get("remark") or "")
        if task.state == "Menxia" and new == "Zhongshu" and task.rejects >= 2:
            # 与 edict 一致：三轮封驳后强制通过
            return _deny(task, r, "kanban_state", "已封驳三轮，按制必须准奏（请推开 Assigned 或回话准奏）")
        ok, why = task.can_move(new)
        if not ok:
            return _deny(task, r, "kanban_state", why)
        info = task.apply_state(new, remark, actor=r.label)
        taskmod.save()
        hub.emit(task.id, "state", role=r.id, label=r.label, **info, remark=remark[:200])
        _audit(task, r, "kanban_state", True, f"{info['from']} → {new}")
        return f"已改：{info['from']} → {new}（现在轮到 {info['org']}）"

    def kanban_flow(args: dict) -> str:
        if (msg := _need("kanban_flow")):
            return msg
        to_dept = str(args.get("to_dept") or "")
        remark = str(args.get("remark") or "")
        task.flow_log.append({"at": taskmod.now_iso(), "from": r.label, "to": to_dept,
                              "from_state": task.state, "to_state": task.state, "remark": remark[:240]})
        taskmod.save()
        hub.emit(task.id, "flow", role=r.id, label=r.label, to=to_dept, remark=remark[:200])
        return f"已记流转：{r.label} → {to_dept or '（未指明）'}"

    def kanban_progress(args: dict) -> str:
        if (msg := _need("kanban_progress")):
            return msg
        text = str(args.get("text") or "").strip()
        plan = str(args.get("plan") or "").strip()
        if plan:
            for i, chunk in enumerate([c.strip() for c in plan.split("|") if c.strip()], start=1):
                done = "✅" in chunk
                doing = "🔄" in chunk
                title = chunk.replace("✅", "").replace("🔄", "").strip()
                item = next((t for t in task.todos if t.get("title") == title), None)
                status = "completed" if done else ("in-progress" if doing else "not-started")
                if item:
                    item["status"] = status
                else:
                    task.todos.append({"id": str(i), "title": title, "status": status, "detail": ""})
        task.add_progress(r.label, text or "（推进一步）")
        taskmod.save()
        hub.emit(task.id, "progress", role=r.id, label=r.label, text=text,
                 plan=plan, todos=[dict(t) for t in task.todos])
        return "已上报进展"

    def kanban_todo(args: dict) -> str:
        if (msg := _need("kanban_todo")):
            return msg
        tid = str(args.get("todo_id") or args.get("id") or "").strip() or str(len(task.todos) + 1)
        title = str(args.get("title") or "").strip() or f"子任务{tid}"
        status = str(args.get("status") or "not-started").strip()
        if status not in ("not-started", "in-progress", "completed"):
            status = "in-progress"
        detail = str(args.get("detail") or "")
        item = next((t for t in task.todos if t.get("id") == tid), None)
        if item:
            item.update({"title": title, "status": status, "detail": detail})
        else:
            task.todos.append({"id": tid, "title": title, "status": status, "detail": detail})
        taskmod.save()
        hub.emit(task.id, "todo", role=r.id, label=r.label, todo_id=tid, title=title,
                 status=status, detail=detail[:300], todos=[dict(t) for t in task.todos])
        return f"子任务 {tid}：{title} → {status}"

    def kanban_done(args: dict) -> str:
        if (msg := _need("kanban_done")):
            return msg
        summary = str(args.get("output") or args.get("summary") or "").strip()
        task.output = summary[:2000]
        taskmod.save()
        hub.emit(task.id, "done_report", role=r.id, label=r.label, summary=summary[:600])
        return "交差已记（状态由尚书省推进）"

    def kanban_block(args: dict) -> str:
        if (msg := _need("kanban_block")):
            return msg
        reason = str(args.get("reason") or "未说明原因")
        ok, _ = task.can_move("Blocked")
        if ok:
            task.apply_state("Blocked", f"阻塞：{reason}", actor=r.label)
            task.block = reason[:300]
            hub.emit(task.id, "state", role=r.id, label=r.label, **{"from": task.state, "to": "Blocked"})
        task.block = reason[:300]
        taskmod.save()
        _audit(task, r, "kanban_block", True, reason[:200])
        hub.emit(task.id, "block", role=r.id, label=r.label, reason=reason[:300])
        return "阻塞已上报"

    # ---------- 4. 差遣（跨部门信息传递的唯一通道）----------

    def call_agent(args: dict) -> str:
        if (msg := _need("call_agent")):
            return msg
        target_id = str(args.get("agent_id") or "").strip()
        instruction = str(args.get("instruction") or "").strip()
        if target_id not in ROLES:
            return f"无此衙门：{target_id}（可用：{', '.join(r.allows)}）"
        if target_id not in r.allows:
            return _deny(task, r, "call_agent",
                         f"{r.label} 无权差遣 {ROLES[target_id].label}（权限图：{', '.join(ROLES[a].label for a in r.allows) or '无'}）")
        if not instruction:
            return "instruction 不能为空"
        if depth + 1 > s.max_depth:
            return _deny(task, r, "call_agent", f"差遣层数超过 {s.max_depth} 层，朝制不许层层转包")
        hub.emit(task.id, "call", role=r.id, label=r.label,
                 to=target_id, to_label=ROLES[target_id].label, instruction=instruction[:1500], depth=depth + 1)
        try:
            answer = run_role(task, target_id, instruction, depth + 1, parent=r.id)
        except BudgetExceeded as exc:
            return f"（{exc}）"
        hub.emit(task.id, "call_return", role=r.id, label=r.label, to=target_id,
                 to_label=ROLES[target_id].label, text=(answer or "")[:2000], depth=depth + 1)
        return (answer or "（对方未回话）")[:12000]

    # ---------- 注册 ----------

    io = {"type": "object", "properties": {"path": {"type": "string", "description": "相对本旨意目录的路径"}}}
    reg.add("list_dir", "列出本旨意沙箱目录里的文件（看同僚产出）", io, list_dir, timeout_ms=15_000)
    reg.add("read_file", "读文件内容（限本旨意沙箱）",
            {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            read_file, timeout_ms=15_000)
    reg.add("write_file", f"写文书到沙箱（你只能写 {r.dept_dir}/ 目录）",
            {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
             "required": ["path", "content"]},
            write_file, timeout_ms=30_000)
    reg.add("kanban_state", "改任务状态（只能往前走一格；非法跳转会被拒）",
            {"type": "object", "properties": {
                "state": {"type": "string", "enum": ["Taizi", "Zhongshu", "Menxia", "Assigned", "Next",
                                                     "Doing", "Review", "Done", "Blocked", "Cancelled",
                                                     "PendingConfirm"]},
                "remark": {"type": "string"}}, "required": ["state"]},
            kanban_state, timeout_ms=15_000)
    reg.add("kanban_flow", "记一条流转（不改变状态）",
            {"type": "object", "properties": {"to_dept": {"type": "string"}, "remark": {"type": "string"}}},
            kanban_flow, timeout_ms=15_000)
    reg.add("kanban_progress", "上报当前进展与计划清单（每一步都该报）",
            {"type": "object", "properties": {
                "text": {"type": "string", "description": "我此刻在做什么"},
                "plan": {"type": "string", "description": "竖线分隔，如 览旨🔄|起草|送审"}},
             "required": ["text"]},
            kanban_progress, timeout_ms=15_000)
    reg.add("kanban_todo", "登记/更新一条子任务",
            {"type": "object", "properties": {
                "todo_id": {"type": "string"}, "title": {"type": "string"},
                "status": {"type": "string", "enum": ["not-started", "in-progress", "completed"]},
                "detail": {"type": "string"}}, "required": ["title"]},
            kanban_todo, timeout_ms=15_000)
    reg.add("kanban_done", "交差：登记本路产出（六部用）",
            {"type": "object", "properties": {"output": {"type": "string"}}}, kanban_done, timeout_ms=15_000)
    reg.add("kanban_block", "报阻塞（办不成时用）",
            {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]},
            kanban_block, timeout_ms=15_000)
    # ---------- 5. 联网读取（scrapling MCP，只给开了 web 的角色）----------
    if r.web:
        for tname, spec in web.tools().items():
            def _mk_mcp(n: str):
                def _handler(args: dict) -> str:
                    return web.call(n, args)
                return _handler
            reg.add(
                tname,
                f"[联网:{spec.server}] {spec.description}",
                spec.parameters,
                _mk_mcp(tname),
                timeout_ms=max(30_000, int(spec.timeout_ms)),
            )

    reg.add("call_agent", "差遣下属衙门办事（按权限图，只有列明的可调）",
            {"type": "object", "properties": {
                "agent_id": {"type": "string", "description": "目标角色 id，如 zhongshu / menxia / shangshu / bingbu"},
                "instruction": {"type": "string", "description": "要什么、交什么格式、写到哪个文件"}},
             "required": ["agent_id", "instruction"]},
            call_agent, timeout_ms=900_000, guard=False)
    return reg


# ── 跑一个角色 ───────────────────────────────────────────────────────────

_depth_guard = threading.local()


def run_role(task, role_id: str, instruction: str, depth: int = 0, parent: str | None = None) -> str:
    s = get_settings()
    r: Role = ROLES[role_id]

    if task.status == "aborted":
        return "（朝会已中止）"
    remain = s.max_llm_calls - (task.llm_calls_round or 0)
    if remain <= 0:
        task.status = "aborted"
        hub.emit(task.id, "error", role="system", message=f"模型调用已达上限 {s.max_llm_calls} 次，罢朝")
        raise BudgetExceeded("模型调用已到上限，罢朝")

    root = task_root(task.id)
    _write_dir(task.id, r)
    fu = task.instructions[-1] if (task.round_no or 1) > 1 and task.instructions else None
    brief_detail = task.detail
    round_note = ""
    if fu:
        round_note = fu.get("title", "")
        brief_detail = (task.detail + "\n\n" if task.detail else "") + f"（第{task.round_no}轮）{fu.get('title', '')}：{fu.get('detail', '')}"
    web_on = bool(r.web and web.available)
    system = r.prompt() + "\n\n" + runtime_brief(
        r, task.id, task.title, brief_detail, root, r.allows,
        round_no=task.round_no or 1, round_note=round_note, web_on=web_on,
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": instruction},
    ]

    hub.emit(task.id, "role_start", role=r.id, label=r.label, name=r.name, emoji=r.emoji,
             depth=depth, parent=parent, round=task.round_no or 1, instruction=instruction[:1200])

    registry = _build_tools(task, r, depth)
    bus = ev.EventBus()
    throttle = StreamThrottle(lambda rid, txt: hub.emit(task.id, "thought", role=rid, text=txt), r.id)

    def on_event(e: "ev.Event") -> None:
        t = e.type
        if t == ev.TEXT_DELTA:
            throttle.add(str(e.data.get("text", "")))
        elif t == ev.TEXT_DONE:
            throttle.flush()
            hub.emit(task.id, "text", role=r.id, label=r.label, text=str(e.data.get("text", ""))[:6000])
        elif t == ev.ROUND_START:
            task.llm_calls += 1
            task.llm_calls_round = (task.llm_calls_round or 0) + 1
            hub.emit(task.id, "round", role=r.id, label=r.label,
                     round=e.data.get("round"), budget_left=e.data.get("budget_left"))
        elif t == ev.TOOL_CALL:
            throttle.flush()
            hub.emit(task.id, "tool_call", role=r.id, label=r.label,
                     name=e.data.get("name"), args=str(e.data.get("args"))[:1200])
        elif t == ev.TOOL_RESULT:
            hub.emit(task.id, "tool_result", role=r.id, label=r.label, name=e.data.get("name"),
                     ok=bool(e.data.get("ok")), content=str(e.data.get("content"))[:1500],
                     ms=e.data.get("ms"))
        elif t == ev.GUARD_NOTICE:
            hub.emit(task.id, "guard", role=r.id, label=r.label, count=e.data.get("count"))
        elif t == ev.ERROR:
            hub.emit(task.id, "error", role=r.id, label=r.label, message=str(e.data.get("message"))[:400])

    bus.subscribe(on_event)
    budget = IterationBudget(max(1, min(r.rounds or s.max_rounds, remain)))
    loop = AgentLoop(provider(), registry, LoopGuard((3, 5, 8)), bus, budget)

    try:
        outcome = loop.run(messages)
    finally:
        throttle.flush()
        registry.close()

    usage = outcome.usage or {}
    p_tok = int(usage.get("prompt_tokens") or 0)
    c_tok = int(usage.get("completion_tokens") or 0)
    task.usage["prompt"] += p_tok
    task.usage["completion"] += c_tok
    hub.emit(task.id, "role_end", role=r.id, label=r.label, depth=depth,
             text=(outcome.final_text or "")[:4000], rounds=outcome.rounds,
             stop_reason=outcome.stop_reason, tokens={"prompt": p_tok, "completion": c_tok})
    return outcome.final_text or ""
