"""事件中枢 —— 朝会上发生的一切都从这里流出去（SSE 的唯一数据源）。

设计纪律：内核/调度器只 emit，从不直接写前端；订阅者断了不影响执行。
每条事件带递增 seq，前端按 seq 去重（先订阅再回放历史时会有重叠）。
"""
from __future__ import annotations

import json
import queue
import threading
import time
from pathlib import Path
from typing import Any

from .config import get_settings

_MAX_HISTORY = 3000


class Hub:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._subs: dict[str, list[queue.Queue]] = {}
        self._history: dict[str, list[dict[str, Any]]] = {}
        self._seq: dict[str, int] = {}
        self._files: dict[str, Any] = {}
        self._dir = Path(get_settings().data_root) / "events"
        self._dir.mkdir(parents=True, exist_ok=True)

    # ---- 落盘（重启后可回放）----
    def _file(self, task_id: str):
        fh = self._files.get(task_id)
        if fh is None:
            safe = "".join(c for c in task_id if c.isalnum() or c in "-_")
            fh = open(self._dir / f"{safe}.jsonl", "a", encoding="utf-8")  # noqa: SIM115
            self._files[task_id] = fh
        return fh

    def _load_from_disk(self, task_id: str) -> list[dict[str, Any]]:
        safe = "".join(c for c in task_id if c.isalnum() or c in "-_")
        p = self._dir / f"{safe}.jsonl"
        out: list[dict[str, Any]] = []
        if p.is_file():
            with p.open("r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    out.append(ev)
                    self._seq[task_id] = max(self._seq.get(task_id, 0), int(ev.get("seq") or 0))
        return out[-_MAX_HISTORY:]

    # ---- 订阅 ----
    def subscribe(self, task_id: str) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=4000)
        with self._lock:
            self._subs.setdefault(task_id, []).append(q)
        return q

    def unsubscribe(self, task_id: str, q: queue.Queue) -> None:
        with self._lock:
            subs = self._subs.get(task_id) or []
            if q in subs:
                subs.remove(q)

    def history(self, task_id: str) -> list[dict[str, Any]]:
        with self._lock:
            if task_id not in self._history:
                self._history[task_id] = self._load_from_disk(task_id)
            return list(self._history.get(task_id, []))

    # ---- 广播 ----
    def emit(self, task_id: str, kind: str, **data: Any) -> dict[str, Any]:
        with self._lock:
            # 老任务（重启后第一次落事件）：先回填盘上历史，避免空历史遮蔽旧事件、seq 从头计
            if task_id not in self._history:
                self._history[task_id] = self._load_from_disk(task_id)
            seq = self._seq.get(task_id, 0) + 1
            self._seq[task_id] = seq
            ev = {"seq": seq, "at": time.strftime("%H:%M:%S"), "ts": time.time(),
                  "kind": kind, **data}
            hist = self._history.setdefault(task_id, [])
            hist.append(ev)
            if len(hist) > _MAX_HISTORY:
                del hist[: len(hist) - _MAX_HISTORY]
            try:
                fh = self._file(task_id)
                fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
                fh.flush()
            except Exception:  # noqa: BLE001 — 落盘失败不该拖垮朝会
                pass
            subs = list(self._subs.get(task_id, []))
        for q in subs:
            try:
                q.put_nowait(ev)
            except queue.Full:
                pass  # 慢客户端直接丢帧，不拖累执行
        return ev

    def drop(self, task_id: str) -> None:
        """彻底丢掉一个任务：关文件句柄、清内存、删事件落盘、断开订阅。"""
        with self._lock:
            fh = self._files.pop(task_id, None)
            if fh is not None:
                try:
                    fh.close()
                except OSError:
                    pass
            self._history.pop(task_id, None)
            self._seq.pop(task_id, None)
            self._subs.pop(task_id, None)
            safe = "".join(c for c in task_id if c.isalnum() or c in "-_")
            p = self._dir / f"{safe}.jsonl"
            if p.is_file():
                try:
                    p.unlink()
                except OSError:
                    pass

    def clear(self, task_id: str) -> None:
        with self._lock:
            self._history.pop(task_id, None)
            self._seq.pop(task_id, None)


hub = Hub()


class StreamThrottle:
    """把逐 token 的思考流合并成 ~150ms 一批，避免 SSE 刷屏。"""

    def __init__(self, emit_fn, role: str, interval: float = 0.15) -> None:
        self._emit = emit_fn
        self._role = role
        self._interval = interval
        self._buf: list[str] = []
        self._last = 0.0

    def add(self, text: str) -> None:
        self._buf.append(text)
        now = time.time()
        if now - self._last >= self._interval:
            self.flush()

    def flush(self) -> None:
        if not self._buf:
            return
        text = "".join(self._buf)
        self._buf.clear()
        self._last = time.time()
        self._emit(self._role, text)
