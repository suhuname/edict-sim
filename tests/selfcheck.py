"""离线自检 —— 不调模型，验证机制本身立得住。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server import task as T  # noqa: E402
from server.kernel import _resolve  # noqa: E402
from server.roles import ROLES, load_prompt  # noqa: E402

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    mark = "PASS" if cond else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def main() -> int:
    print("=== 状态机 ===")
    t = T.Task(id="JJC-TEST-001", title="自检")
    check("Pending→Taizi 合法", t.can_move("Taizi")[0])
    t.state = "Doing"
    ok, why = t.can_move("Taizi")
    check("Doing→Taizi 非法被拒", not ok, why)
    ok, why = t.can_move("Review")
    check("Doing→Review 合法", ok, why)
    t.state = "Menxia"
    t.rejects = 2
    ok, _ = t.can_move("Zhongshu")
    check("第三轮封驳仍合法但工具层会拦", ok)

    print("=== 回奏（续轮）===")
    r = T.Task(id="JJC-TEST-002", title="续办", state="Done", round_no=1, llm_calls=73, rejects=2)
    ok, why = r.can_move("Taizi")
    check("Agent 不能把终态推回太子", not ok, why)
    info = r.reopen("补一版日计划", "按 Day1-Day5")
    check("回奏后回到 Taizi", r.state == "Taizi" and info["from"] == "Done")
    check("轮次 +1", r.round_no == 2 and info["round"] == 2)
    check("封驳计数与轮内预算清零", r.rejects == 0 and r.llm_calls_round == 0)
    check("累计调用不清零（账要连着记）", r.llm_calls == 73)
    check("流程日志记为皇上→太子", r.flow_log[-1]["from"] == "皇上" and r.flow_log[-1]["to_state"] == "Taizi")
    check("本轮圣意已入册", r.instructions[-1]["title"] == "补一版日计划")
    check("续办后 Taizi→Zhongshu 合法", r.can_move("Zhongshu")[0])

    print("=== 沙箱边界 ===")
    root = (ROOT / "workspace" / "_selfcheck").resolve()
    root.mkdir(parents=True, exist_ok=True)
    check("相对路径可解析", _resolve(root, "户部/核算.md").is_relative_to(root))
    for bad in ("../../etc/passwd", "..\\..\\windows\\system32", "C:/Windows/system.ini", "/etc/passwd"):
        try:
            p = _resolve(root, bad)
            check(f"越界被拒: {bad}", False, f"竟然解析到 {p}")
        except ValueError:
            check(f"越界被拒: {bad}", True)

    print("=== 权限图 / 工具白名单 ===")
    check("六部无改状态权", "kanban_state" not in ROLES["hubu"].policy)
    check("三省有改状态权", "kanban_state" in ROLES["zhongshu"].policy)
    check("太子只能差遣中书", ROLES["taizi"].allows == ["zhongshu"])
    check("尚书可差遣六部", set(ROLES["shangshu"].allows) >= {"hubu", "libu", "bingbu", "xingbu", "gongbu", "libu_hr"})
    check("六部不能差遣他人", ROLES["bingbu"].allows == [])
    check("门下可回调中书（封驳）", "zhongshu" in ROLES["menxia"].allows)
    check("六部写目录各不同", len({ROLES[r].dept_dir for r in ("hubu", "libu", "bingbu", "xingbu", "gongbu", "libu_hr")}) == 6)

    print("=== 联网权限 ===")
    check("六部与三省可联网", ROLES["bingbu"].web and ROLES["zhongshu"].web and ROLES["menxia"].web and ROLES["shangshu"].web)
    check("太子/旁观官不联网", not ROLES["taizi"].web and not ROLES["zaochao"].web)

    print("=== 提示词三层 ===")
    for rid in ROLES:
        p = load_prompt(ROLES[rid])
        check(f"{rid} 提示词非空且含全局朝规", len(p) > 400 and "铁律" in p, f"len={len(p)}")

    print()
    if FAILED:
        print(f"❌ {len(FAILED)} 项失败: {', '.join(FAILED)}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
