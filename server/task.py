"""任务模型 + 状态机 —— 逐条对齐 edict 的 edict/backend/app/models/task.py。

设计纪律（照抄原项目）：
- **状态机是唯一事实源**：非法跳转一律拒绝，Agent 只能在当前状态里往前推一格。
- 看板（这里落成 data/tasks.json）是唯一共享状态；所有角色通过 kanban_* 工具改它。
- 每次变更都写 flow_log / progress_log / audit，链路可回放。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import threading
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from .config import get_settings

# ── 状态机（与 edict 完全一致）──────────────────────────────────────────────

STATES = [
    "Pending", "Taizi", "Zhongshu", "Menxia", "Assigned",
    "Next", "Doing", "Review", "Done", "Blocked", "Cancelled", "PendingConfirm",
]

TERMINAL_STATES = {"Done", "Cancelled"}

STATE_TRANSITIONS: dict[str, set[str]] = {
    "Pending": {"Taizi", "Cancelled"},
    "Taizi": {"Zhongshu", "Cancelled"},
    "Zhongshu": {"Menxia", "Cancelled", "Blocked"},
    "Menxia": {"Assigned", "Zhongshu", "Cancelled"},
    "Assigned": {"Doing", "Next", "Cancelled", "Blocked"},
    "Next": {"Doing", "Cancelled", "Blocked"},
    "Doing": {"Review", "Done", "Blocked", "Cancelled"},
    "Review": {"Done", "Menxia", "Doing", "Cancelled", "PendingConfirm"},
    "PendingConfirm": {"Done", "Review", "Cancelled"},
    "Blocked": {"Taizi", "Zhongshu", "Menxia", "Assigned", "Next", "Doing", "Review", "Cancelled"},
}

# 状态 → 承担该状态的角色
STATE_ROLE = {
    "Pending": "zhongshu",
    "Taizi": "taizi",
    "Zhongshu": "zhongshu",
    "Menxia": "menxia",
    "Assigned": "shangshu",
    "Review": "shangshu",
    "PendingConfirm": "shangshu",
}

# 状态 → 看板上显示的部门
STATE_ORG = {
    "Pending": "中书省", "Taizi": "太子", "Zhongshu": "中书省", "Menxia": "门下省",
    "Assigned": "尚书省", "Review": "尚书省", "PendingConfirm": "尚书省",
    "Done": "完成", "Cancelled": "已废", "Blocked": "阻塞",
}

ORG_ROLE = {
    "户部": "hubu", "礼部": "libu", "兵部": "bingbu",
    "刑部": "xingbu", "工部": "gongbu", "吏部": "libu_hr",
    "中书省": "zhongshu", "门下省": "menxia", "尚书省": "shangshu", "太子": "taizi",
}

# 高风险的"改命"式跳转：需要留下明确的理由（照抄 edict 的 HIGH_RISK_TRANSITIONS 精神）
HIGH_RISK = {("Doing", "Taizi"), ("Review", "Zhongshu"), ("Done", "Doing")}

# 「回奏」：不是新开一道旨意，而是在原旨意上续一轮。
# 只能由皇上（HTTP 接口）触发；Agent 调 kanban_state 仍然过不去（终态不可流转）。
REOPEN_FROM = {"Done", "Blocked", "Cancelled"}


def now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


# ── 任务 ─────────────────────────────────────────────────────────────────


@dataclass
class Task:
    id: str
    title: str
    detail: str = ""
    state: str = "Pending"
    org: str = "中书省"
    official: str = ""
    now: str = ""
    block: str = ""
    output: str = ""
    ac: str = ""
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    flow_log: list[dict[str, Any]] = field(default_factory=list)
    progress_log: list[dict[str, Any]] = field(default_factory=list)
    todos: list[dict[str, Any]] = field(default_factory=list)
    audit: list[dict[str, Any]] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    rejects: int = 0            # 门下省封驳次数（>=3 强制通过）
    round_no: int = 1           # 第几轮（1=初旨；>1=回奏续办）
    instructions: list[dict[str, Any]] = field(default_factory=list)  # 每轮圣旨/回奏原文
    llm_calls_round: int = 0    # 本轮已用模型调用（预算按轮计）
    llm_calls: int = 0
    usage: dict[str, int] = field(default_factory=lambda: {"prompt": 0, "completion": 0})
    status: str = "idle"        # idle | running | finished | aborted
    note: str = ""              # 收尾说明（未回奏的原因等）
    finished: bool = False

    # ---- 序列化 ----
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    # ---- 状态迁移 ----
    def can_move(self, new_state: str) -> tuple[bool, str]:
        if new_state not in STATES:
            return False, f"未知状态 {new_state}"
        if self.state in TERMINAL_STATES:
            return False, f"任务已终态（{self.state}），不可再流转"
        allowed = STATE_TRANSITIONS.get(self.state, set())
        if new_state not in allowed:
            return False, f"非法跳转：{self.state} → {new_state}（允许：{', '.join(sorted(allowed))}）"
        return True, ""

    def apply_state(self, new_state: str, remark: str = "", actor: str = "") -> dict[str, Any]:
        """执行一次迁移，返回派发信息。调用方负责写盘与广播。"""
        old = self.state
        self.state = new_state
        self.updated_at = now_iso()
        if remark:
            self.now = remark[:200]
        self.org = self._org_for_state(new_state)
        self.flow_log.append({
            "at": self.updated_at, "from": actor or old, "to": self.org,
            "from_state": old, "to_state": new_state, "remark": remark[:240],
        })
        if new_state == "Menxia":
            pass
        if old == "Menxia" and new_state == "Zhongshu":
            self.rejects += 1
        return {"from": old, "to": new_state, "org": self.org}

    def _org_for_state(self, state: str) -> str:
        if state in ("Doing", "Next"):
            return self.org if self.org not in ("中书省", "门下省", "尚书省", "太子", "完成", "阻塞") else "六部"
        return STATE_ORG.get(state, self.org)

    def reopen(self, title: str, detail: str) -> dict[str, Any]:
        """回奏：同一道旨意续一轮，从太子重新走流程。"""
        old = self.state
        self.round_no += 1
        self.llm_calls_round = 0
        self.rejects = 0
        self.finished = False
        self.note = ""
        self.block = ""
        self.status = "running"
        self.state = "Taizi"
        self.org = "太子"
        self.now = "回奏已下，太子分拣中"
        self.updated_at = now_iso()
        self.instructions.append({
            "round": self.round_no, "kind": "followup", "title": title[:120],
            "detail": detail[:4000], "at": self.updated_at, "from_state": old, "md": "",
        })
        self.flow_log.append({
            "at": self.updated_at, "from": "皇上", "to": "太子",
            "from_state": old, "to_state": "Taizi",
            "remark": f"回奏·第{self.round_no}轮：{title[:80]}",
        })
        self.log("皇上", "followup", True, f"第{self.round_no}轮：{title[:120]}")
        return {"from": old, "to": "Taizi", "round": self.round_no}

    def add_progress(self, role_label: str, text: str, todos: list[dict[str, Any]] | None = None) -> None:
        self.updated_at = now_iso()
        self.now = text[:200]
        self.progress_log.append({
            "at": self.updated_at, "agent": role_label, "text": text[:500],
            "state": self.state, "org": self.org,
            "todos": todos if todos is not None else [dict(t) for t in self.todos],
        })
        if len(self.progress_log) > 200:
            self.progress_log = self.progress_log[-200:]

    def log(self, actor: str, action: str, ok: bool = True, detail: str = "") -> None:
        self.audit.append({"at": now_iso(), "agent": actor, "action": action,
                           "ok": ok, "detail": detail[:300]})
        if len(self.audit) > 400:
            self.audit = self.audit[-400:]


# ── 存储 ─────────────────────────────────────────────────────────────────

_lock = threading.RLock()
_tasks: dict[str, Task] = {}
_loaded = False


def _store_path() -> Path:
    return get_settings().data_root / "tasks.json"


def _load() -> None:
    global _loaded
    if _loaded:
        return
    p = _store_path()
    if p.is_file():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            for item in raw:
                t = Task(**item)
                _tasks[t.id] = t
        except Exception:  # noqa: BLE001 — 存档坏了不该拦住服务
            pass
    _loaded = True


def save() -> None:
    with _lock:
        p = _store_path()
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps([t.to_dict() for t in _tasks.values()], ensure_ascii=False, indent=1),
                       encoding="utf-8")
        os.replace(tmp, p)


def new_task(title: str, detail: str = "") -> Task:
    _load()
    with _lock:
        today = _dt.date.today().strftime("%Y%m%d")
        n = 1 + sum(1 for t in _tasks.values() if t.id.startswith(f"JJC-{today}"))
        t = Task(id=f"JJC-{today}-{n:03d}", title=title[:120], detail=detail[:4000],
                 official="中书令", ac="")
        t.instructions.append({
            "round": 1, "kind": "decree", "title": title[:120], "detail": detail[:4000],
            "at": t.created_at, "md": "",
        })
        t.flow_log.append({"at": t.created_at, "from": "皇上", "to": "太子",
                           "from_state": "", "to_state": "Pending", "remark": "颁旨"})
        _tasks[t.id] = t
        save()
        return t


def get(task_id: str) -> Task | None:
    _load()
    return _tasks.get(task_id)


def all_tasks() -> list[Task]:
    _load()
    return sorted(_tasks.values(), key=lambda t: t.created_at, reverse=True)


def delete_task(task_id: str) -> dict[str, Any] | None:
    """从看板移除一条旨意（连带把删除动作记进全局删除日志）。"""
    _load()
    with _lock:
        t = _tasks.pop(task_id, None)
        if t is None:
            return None
        save()
        record = {"at": now_iso(), "id": t.id, "title": t.title, "state": t.state,
                  "round_no": t.round_no, "files": len(t.files)}
        try:
            log = get_settings().data_root / "deleted.jsonl"
            with log.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass
        return record


def reset() -> None:
    """仅供自检用。"""
    global _loaded
    with _lock:
        _tasks.clear()
        _loaded = True
        p = _store_path()
        if p.is_file():
            p.unlink()
