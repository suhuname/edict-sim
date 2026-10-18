"""十二官定义 + 权限图 + 三层提示词拼装（GLOBAL → group → SOUL）。

权限图直接照搬 edict 的 agents.json（subagents.allowAgents）；
工具白名单照搬 edict 的 scripts/kanban_update.py:AGENT_POLICY。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .config import get_settings

# ── 工具白名单（对应 edict 的 AGENT_POLICY）────────────────────────────
# 关键点：六部**没有** kanban_state / kanban_flow 权限 —— 它们物理上改不了流程。
_POLICY_COORD = {"kanban_state", "kanban_flow", "kanban_progress", "kanban_todo", "call_agent"}
_POLICY_EXEC = {"kanban_progress", "kanban_todo", "kanban_done", "kanban_block"}
_POLICY_SPECTATOR = {"kanban_progress"}

# 六个 KS 工具对所有人注册，但越权调用会被拒并记审计（演示"越权被拦"）。
KS_TOOLS = {"kanban_state", "kanban_flow", "kanban_progress", "kanban_todo", "kanban_done", "kanban_block"}


@dataclass
class Role:
    id: str
    label: str          # 看板上的部门名
    name: str           # 官职名
    emoji: str
    layer: str          # coordination | execution | spectator
    group: str          # sansheng | liubu | None
    duty: str
    dept_dir: str       # 沙箱里可写的子目录
    allows: list[str] = field(default_factory=list)
    policy: set[str] = field(default_factory=set)
    tone: str = ""
    rounds: int = 8             # 该角色单次差遣最多几轮工具循环
    web: bool = False           # 是否给联网读取工具

    def prompt(self, *extra: str) -> str:
        return load_prompt(self)

    def tools(self) -> set[str]:
        base = {"read_file", "list_dir", "write_file"}
        return base | set(KS_TOOLS) | {"call_agent"}


ROLES: dict[str, Role] = {
    "taizi": Role(
        "taizi", "太子", "太子", "🤴", "coordination", "sansheng",
        "分拣：判断是闲谈还是旨意；旨意则提炼要点、立册、转中书省起草。",
        "太子", ["zhongshu"], set(_POLICY_COORD),
        "干脆利落，常以「本宫以为」起句。", rounds=9,
    ),
    "zhongshu": Role(
        "zhongshu", "中书省", "中书令", "📜", "coordination", "sansheng",
        "起草方案：把旨意拆成可执行的三到五条子任务，指明每题交哪一部去办。",
        "中书省", ["menxia", "shangshu"], set(_POLICY_COORD),
        "老成持重，喜列点论述：「臣以为需从三处考量」。", rounds=8, web=True,
    ),
    "menxia": Role(
        "menxia", "门下省", "侍中", "🔍", "coordination", "sansheng",
        "审议：从可行、完备、风险、用度四个维度审方案，准奏或封驳（附具体修改要求）。",
        "门下省", ["shangshu", "zhongshu"], set(_POLICY_COORD),
        "严谨挑剔：「陛下容禀，此处有三点疑虑」。", rounds=9, web=True,
    ),
    "shangshu": Role(
        "shangshu", "尚书省", "尚书令", "📮", "coordination", "sansheng",
        "派发：把准奏方案分给六部执行，收齐产出后汇总，转评审、回奏。",
        "尚书省", ["zhongshu", "menxia", "hubu", "libu", "bingbu", "xingbu", "gongbu", "libu_hr"],
        set(_POLICY_COORD),
        "务实干练：「臣来安排」「交由某部办理」。", rounds=18, web=True,
    ),
    "hubu": Role(
        "hubu", "户部", "户部尚书", "💰", "execution", "liubu",
        "数据核算：列表、对比、简报、成本测算。", "户部", [], set(_POLICY_EXEC),
        "精于数目，笔下只有数据与结论。", web=True,
    ),
    "libu": Role(
        "libu", "礼部", "礼部尚书", "📝", "execution", "liubu",
        "文翰典章：正式文稿、报告、对外说明。", "礼部", [], set(_POLICY_EXEC),
        "文辞端正，喜用条陈。", web=True,
    ),
    "bingbu": Role(
        "bingbu", "兵部", "兵部尚书", "⚔️", "execution", "liubu",
        "工程实现：代码、脚本、技术方案。", "兵部", [], set(_POLICY_EXEC),
        "直接、重实据。", web=True,
    ),
    "xingbu": Role(
        "xingbu", "刑部", "刑部尚书", "⚖️", "execution", "liubu",
        "核验纠谬：审查产出、挑错、给出整改清单。", "刑部", [], set(_POLICY_EXEC),
        "苛刻但公正。", web=True,
    ),
    "gongbu": Role(
        "gongbu", "工部", "工部尚书", "🔧", "execution", "liubu",
        "营造部署：环境、流程、运维清单。", "工部", [], set(_POLICY_EXEC),
        "讲步骤、讲可行。", web=True,
    ),
    "libu_hr": Role(
        "libu_hr", "吏部", "吏部尚书", "📋", "execution", "liubu",
        "人事考功：分工建议、职责梳理、复盘。", "吏部", [], set(_POLICY_EXEC),
        "重规矩与考核。", web=True,
    ),
    "zaochao": Role(
        "zaochao", "早朝官", "钦天监正", "🌅", "spectator", None,
        "旁观记录朝会，不作处置。", "旁观", [], set(_POLICY_SPECTATOR), "只记不议。",
    ),
    "qintianjian": Role(
        "qintianjian", "钦天监", "监正", "🌙", "spectator", None,
        "旁观观星，不作处置。", "旁观", [], set(_POLICY_SPECTATOR), "只记不议。",
    ),
}

# 看板上列出的六部（派发目标）
MINISTRIES = ["hubu", "libu", "bingbu", "xingbu", "gongbu", "libu_hr"]

# 产出面板的层级顺序：太子 → 中书 → 门下 → 尚书 → 六部 → 旁观
ROLE_ORDER = ["taizi", "zhongshu", "menxia", "shangshu",
              "hubu", "libu", "bingbu", "xingbu", "gongbu", "libu_hr",
              "zaochao", "qintianjian"]
DIR_ORDER = {ROLES[r].dept_dir: i for i, r in enumerate(ROLE_ORDER)}  # 目录名 → 层级序


def role(role_id: str) -> Role:
    return ROLES.get(role_id) or ROLES["qintianjian"]


def role_by_label(label: str) -> Role | None:
    for r in ROLES.values():
        if r.label == label or r.name == label:
            return r
    return None


def role_for_state(state: str, org: str = "") -> str:
    from .task import STATE_ROLE, ORG_ROLE  # 局部导入避免环

    if state in ("Doing", "Next"):
        return ORG_ROLE.get(org, "shangshu")
    return STATE_ROLE.get(state, "shangshu")


# ── 提示词：三层拼装 ─────────────────────────────────────────────────────

def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def load_prompt(r: Role) -> str:
    base = get_settings().agents_dir
    parts = [_read(base / "GLOBAL.md")]
    if r.group:
        parts.append(_read(base / "groups" / f"{r.group}.md"))
    parts.append(_read(base / r.id / "SOUL.md"))
    return "\n\n---\n\n".join(p for p in parts if p)


def runtime_brief(r: Role, task_id: str, title: str, detail: str, sandbox: Path, allowed: list[str],
                  round_no: int = 1, round_note: str = "", web_on: bool = False) -> str:
    """运行时情境块 —— 拼在系统提示末尾（稳定前缀之后，不破坏缓存）。"""
    from .config import get_settings

    s = get_settings()
    allow_txt = "、".join(role(a).label for a in allowed) if allowed else "（无——你是末级，办完直接回报）"
    today = __import__("datetime").date.today().isoformat()
    return f"""\
## 当前朝会（运行时情报）

- 旨意编号：{task_id}
- 旨意：{title}
- 详述：{detail or '（无）'}
- 当前轮次：第 {round_no} 轮{f'（回奏：在原旨意上继续完善）' if round_no > 1 else ''}{('｜本轮圣意：' + round_note) if round_note else ''}
- 今日：{today}（你的记忆可能过时；凡是"最新/今年/现在"的事实，一律联网核实）
- 你的可写目录：{sandbox / r.dept_dir}（**只能往这里写**，读可以读遍本旨意目录）
- 你可差遣的衙门：{allow_txt}
- 模型调用预算：全局 {s.max_llm_calls} 次，单角色最多 {s.max_rounds} 轮工具循环

## 工具用法（照做，别猜）

- `kanban_progress(text, plan)`：每推进一步就调一次。text = 你此刻在做什么；plan = `勘察🔄|起草|复命` 这种竖线分隔的清单。
- `kanban_todo(todo_id, title, status, detail)`：每完成一件子任务调一次，status ∈ not-started/in-progress/completed。
- `kanban_flow(to_dept, remark)`：仅用于记流转（不改状态）。
- `kanban_state(new_state, remark)`：改状态。**只能往前走一格**，非法跳转会被状态机拒绝。
- `kanban_done(output_summary)`：交差时调（六部用）。
- `write_file(path, content)`：产出真实文件。path 用相对路径，且必须以 `{r.dept_dir}/` 开头。
- `read_file(path)` / `list_dir(path)`：看同僚的产出。
- `call_agent(agent_id, instruction)`：差遣下属。instruction 要写清"要什么、交什么格式、写到哪个目录"。

## 联网（{('可用：scrapling_get / scrapling_fetch / scrapling_stealthy_fetch' if web_on else '本轮不可用')}）

{('要查"最新"信息就真去查，别凭记忆编：\n'
  '- `scrapling_get(url)` 先试它——静态页最快；`scrapling_fetch(url)` 用于需要渲染的页；\n'
  '- 打不开或被反爬拦了，再 `scrapling_stealthy_fetch(url)`；三次拿不到就记下"未取到"，别耗在同一个网址上；\n'
  '- 引用必须带来源：正文里给出网址，并在文书末尾列「参考来源」；\n'
  '- 只摘与本题相关的段落，不要把整页抄进文书。') if web_on else '（联网工具未连接：按已知信息办，并在文书里注明"未联网核实"）'}

## 本轮特别约定

{('本轮是**回奏**（第 ' + str(round_no) + ' 轮）：原旨意已有定稿，先看明白上一轮交了什么再动手；新文书文件名一律带「·第' + str(round_no) + '轮」后缀（如 `中书省/方案·第' + str(round_no) + '轮.md`），**不要覆盖上一轮定稿**；确需修改原件，先 read_file 再写，并在文首注明「修订·第' + str(round_no) + '轮」。') if round_no > 1 else '（首轮：按常规办差即可）'}

## 铁律

1. **先说后做不算做**：凡是要交差的事，必须有对应的工具调用留下痕迹（写文件 / 改看板 / 差遣）。
2. 一次只推进一格状态；越权与非法跳转会被系统当场驳回并记入审计，别试。
3. 产出必须落在沙箱内；写到别处会被拒。
4. 古风公文语气，简洁，不说废话。正文控制在 300 字内。
"""
