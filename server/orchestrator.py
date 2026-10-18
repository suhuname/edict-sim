"""调度中枢 —— 一道旨意从立册走到回奏。

与 edict 的差别（刻意的）：
    edict 靠 `openclaw agent` 子进程派发，每个 agent 一次独立调用；
    这里角色之间靠 `call_agent` 工具**嵌套跑同一个回合循环**，
    所以"信息传递"是真实的调用栈，不是定时脚本摆出来的动画。

仍然保留原项目的三条硬规矩：
    1. 状态机把关，非法跳转拒绝；
    2. 协议校验 + 纠正轮（模型不守流程时拉回来）；
    3. 全程审计（越权、越界、非法跳转都留痕）。
"""
from __future__ import annotations

import threading
import time

from . import task as taskmod
from .config import get_settings
from .hub import hub
from .kernel import BudgetExceeded, run_role, write_instruction
from .roles import ROLES, role as get_role, role_for_state

_threads: dict[str, threading.Thread] = {}
_lock = threading.Lock()
MAX_CORRECTIONS = 3

# 纠正轮该找谁：状态 → 角色（Menxia 由门下自己推进，Review 由中书收尾）
_CORRECTIVE = {
    "Pending": "taizi", "Taizi": "taizi", "Zhongshu": "zhongshu", "Menxia": "menxia",
    "Assigned": "shangshu", "Next": "shangshu", "Doing": "shangshu", "Review": "zhongshu",
}
# 第二棒仍无进展时，换个人来救（避免同一个角色反复不作为）
_FALLBACK = {"Menxia": "zhongshu", "Review": "shangshu", "Zhongshu": "menxia", "Assigned": "zhongshu"}



def start(task) -> None:
    """在后台线程里开朝会（不阻塞 HTTP 请求）。"""
    with _lock:
        th = _threads.get(task.id)
        if th and th.is_alive():
            return
        th = threading.Thread(target=_run, args=(task.id,), daemon=True, name=f"court-{task.id}")
        _threads[task.id] = th
        th.start()


def abort(task_id: str) -> None:
    t = taskmod.get(task_id)
    if not t:
        return
    t.status = "aborted"
    hub.emit(t.id, "audit", role="system", label="内侍", action="abort", ok=True, detail="陛下叫停")
    taskmod.save()


def _run(task_id: str) -> None:
    t = taskmod.get(task_id)
    if not t:
        return
    is_followup = t.round_no > 1
    fu = (t.instructions[-1] if is_followup and t.instructions else None)
    t.status = "running"
    t.finished = False
    t.llm_calls_round = 0
    taskmod.save()
    t0 = time.time()
    # 本轮圣旨/回奏原文先落盘，弹窗与总线都能点开看原文
    if is_followup and fu:
        write_instruction(t, t.round_no, "followup", fu.get("title", ""), fu.get("detail", ""))
    else:
        write_instruction(t, 1, "decree", t.title, t.detail)
    hub.emit(t.id, "court_start", title=t.title, detail=t.detail, model=get_settings().model,
             round=t.round_no, followup=is_followup,
             round_title=(fu or {}).get("title", ""),
             sandbox=str(get_settings().sandbox_root / t.id))

    def emit_end(note: str = "") -> None:
        # 未回奏就收场（预算耗尽/模型不守流程）：如实记账，并尽量转 Blocked 等人工
        if t.state not in taskmod.TERMINAL_STATES and not note:
            note = f"朝会未竟：停在 {t.state}（{t.org}），未回奏"
        if t.state not in taskmod.TERMINAL_STATES and note:
            ok, _ = t.can_move("Blocked")
            if ok:
                info = t.apply_state("Blocked", note[:150], actor="内侍")
                hub.emit(t.id, "state", role="system", label="内侍", remark=note[:150], **info)
                t.block = note[:300]
        t.note = (note or "")[:300]
        t.status = "finished"
        t.finished = True
        taskmod.save()
        hub.emit(t.id, "court_end", state=t.state, org=t.org,
                 seconds=round(time.time() - t0, 1), llm_calls=t.llm_calls,
                 usage=dict(t.usage), files=list(t.files), note=t.note)

    try:
        # ── 第一棒：太子分拣（初旨 or 回奏续办）──
        if is_followup and fu:
            first = (
                f"陛下就本站原旨意（{t.id}）交办的结果再下旨 —— 这是**回奏·第{t.round_no}轮**，"
                f"不是新开一道旨意，而是在原旨意上继续完善。\n\n"
                f"本轮圣意：{fu.get('title', '')}\n"
                + (f"\n详述：{fu.get('detail', '')}\n" if fu.get("detail") else "")
                + "\n请你以太子身份分拣这道回奏：先用 list_dir / read_file 看上一轮已交的文书"
                  "（重点看 尚书省/ 的汇总，以及相关部院的产出），弄清本轮要补强什么、改什么；"
                  f"然后立册于 太子/旨意提要·第{t.round_no}轮.md，状态已在 Taizi，直接推到 Zhongshu，"
                  f"再差遣中书省起草本轮方案。\n"
                  f"⚠️ 本轮所有新文书文件名请带「·第{t.round_no}轮」后缀，不要覆盖上一轮定稿。"
            )
        else:
            first = (
                f"陛下颁旨（旨意编号 {t.id}）：\n{t.title}\n"
                + (f"\n详述：{t.detail}\n" if t.detail else "")
                + "\n请你以太子身份分拣此旨：提炼要点、立册于太子/旨意提要.md、"
                  "把状态推到 Zhongshu，然后差遣中书省起草方案。"
            )
        run_role(t, "taizi", first, depth=0)

        # ── 协议校验：没走到终态就拉回来（最多三轮，换人救场） ──
        for attempt in range(MAX_CORRECTIONS):
            if t.state in taskmod.TERMINAL_STATES or t.status == "aborted":
                break
            prev_state = t.state
            rid = _CORRECTIVE.get(t.state) or role_for_state(t.state, t.org)
            if attempt == 1:
                rid = _FALLBACK.get(t.state, rid)
            r = get_role(rid)
            hub.emit(t.id, "audit", role="system", label="内侍",
                     action="protocol_check", ok=False,
                     detail=f"朝会停在 {t.state}（{t.org}），补派 {r.label}")
            nudge = (
                f"朝会眼下停在「{t.state}」（{t.org}），尚未回奏。按制该由你（{r.label}）接手：{r.duty}\n"
                f"当前旨意：{t.title}（第{t.round_no}轮）\n"
                f"你手上的权限：{'、'.join(sorted(x for x in r.policy))}\n"
                "该改状态就用 kanban_state 推到下一格（Pending→Taizi→Zhongshu→Menxia→Assigned→Doing→Review→Done），"
                "该派差就用 call_agent。必要时先 list_dir / read_file 看沙箱里已有的文书。不要空谈，也不要重复已完成的步骤。"
            )
            run_role(t, rid, nudge, depth=0)
            if t.state == prev_state and t.state not in taskmod.TERMINAL_STATES:
                t.log("系统", "no_progress", ok=False, detail=f"补派 {r.label} 后仍停在 {t.state}")

        emit_end()
    except BudgetExceeded as exc:
        emit_end(str(exc))
    except Exception as exc:  # noqa: BLE001 — 调度器不能把服务带崩
        t.log("系统", "crash", ok=False, detail=f"{type(exc).__name__}: {exc}")
        hub.emit(t.id, "error", role="system", message=f"{type(exc).__name__}: {exc}")
        emit_end(f"异常终止：{exc}")
