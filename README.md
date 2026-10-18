# 三省六部 · 朝堂信息流

把 [edict](https://github.com/cft0808/edict)（三省六部原项目）那套多 Agent 制度**做成能亲眼看着它跑**的 Web 演示：
十二个角色由真模型（DeepSeek）驱动，按状态机和权限图互相差遣、写文书、查账、驳回，
所有产出落在一个沙箱目录里，全过程实时流到浏览器。

它不是一个聊天框，而是一台**能看见制度怎么运作的机器**。

> 在线仓库：<https://github.com/suhuname/edict-sim> · 许可 MIT · 本机自用，只监听 127.0.0.1

---

## 它到底演示了什么

| 机制 | 在界面上能看到什么 |
|---|---|
| **状态机把关** | 太子想把 `Pending` 直接跳到 `Zhongshu` → 被拒，审计里出现一条朱批「非法跳转」，它只能老老实实先推 `Taizi` |
| **分权制衡** | 六部尝试 `kanban_state` / `kanban_flow` → 当场驳回（它们物理上没有这个工具权限） |
| **真派发链** | 太子 `call_agent` → 中书 → 门下 → 尚书 → 六部，总线里是一条条真实的差遣与回话，不是脚本定时器 |
| **门下封驳** | 中书方案被门下挑出缺陷 → `Menxia → Zhongshu` 退回重写 → 改完再送审（最多三轮，第三轮强制通过） |
| **沙箱边界** | 兵部想把文件塞进别人的目录 → 被拒并记审计，它自己改写到 `兵部/`，还在文书里写明"此处被驳" |
| **真产出** | 六部在 `workspace/<旨意号>/` 下写真文件（周报、对比表、代码、审查清单），尚书 `read_file` 读回汇总，可点开预览原文 |
| **联网查证** | 中书/门下/尚书 + 六部可用 `scrapling_get / fetch / stealthy_fetch` 真去查网页，文书里带网址与「参考来源」；抓不到就写「未取到」，不许编 |
| **回奏续办** | 一轮办完（回奏/Done）后，皇上可在**同一道旨意上续一轮**：状态退回太子，三省六部重新走一遍，产出叠加在原目录（文件名带「·第N轮」），不新开一条线 |
| **全链路可回放** | 每个事件带 `seq` 落盘到 `data/events/*.jsonl`，重启后仍能把整场朝会重放一遍 |

实测一场（3 个部门、17 份文书）：**77 次模型调用 / 19.2 万 prompt + 2.8 千 completion tokens / 7.6 分钟**。
DeepSeek Flash 下每次演示花费约几分钱。

同一场里被系统当场驳回的 7 次「朱批」：

| 谁 | 想干什么 | 结果 |
|---|---|---|
| 太子 | `Pending → Zhongshu` 一步到位 | 非法跳转，只允许 `Taizi` |
| 兵部 / 工部 | 把文书往别人的目录里写 | 越界，只能写自己职能目录 |
| 兵部 / 工部 | 用 `kanban_flow` 记流转 | 六部无此权限 |
| 中书省 | 任务 `Done` 之后再改状态 | 终态不可流转 |

---

## 快速开始

```bash
# 1. 依赖（Python 3.11+）
pip install fastapi uvicorn httpx pyyaml

# 2. 密钥
echo DEEPSEEK_API_KEY=sk-xxxx > .env

# 3. 启动（Windows 双击 start.bat 等价）
python main.py            # 默认 http://127.0.0.1:9031

# 4. 离线自检（不调模型：状态机 / 沙箱 / 权限图 / 提示词 / 联网授权）
python main.py test
```

打开 <http://127.0.0.1:9031> → 点一个预设 → 「颁旨」→ 看着朝会开起来。

**Python 解释器怎么选**：`start.bat` 与 `main.py` 按 `EDICT_PYTHON` → 项目同级 `.venv` → 当前 `python` 的顺序找；
想指定某个环境就 `set EDICT_PYTHON=C:\path\to\python.exe`。

### 依赖说明（重要）

本项目自身不含 agent 内核，而是通过 `config.yaml: kernel.dir` 指向一个 **agent-core 兼容内核**，
从里面借 `AgentLoop`（回合循环）/ `ToolRegistry`（工具契约）/ `LoopGuard` / `IterationBudget` / `EventBus` / OpenAI 兼容 provider / MCP 客户端。

```
<你的工作区>/
├── edict-sim/      本项目
├── agent-core/     内核（kernel.dir 默认指向同级目录）
└── Scrapling-main/ 可选：联网用的 scrapling MCP（含 .venv）
```

- `kernel.dir` 默认 `../agent-core`，可用环境变量 `EDICT_KERNEL_DIR` 覆盖
- 内核需要暴露的接口就上面那几个模块名；想换成自己的实现，改 `server/kernel.py` 的 import 即可（全项目只在这一处依赖内核）
- **联网是可选的**：没有 scrapling 就设 `mcp.enabled: false`，其余功能照跑

### 联网（可选）

```bash
pip install "scrapling[all]"      # 或用自己的 venv，然后指路：
set EDICT_MCP_PYTHON=<装了 mcp SDK 的 python>
set EDICT_SCRAPLING=<scrapling.exe>
```

`config.yaml` 里 `mcp.servers` 会经 agent-core 的 `tools/mcp_gateway.py` 起一个常驻网关（默认 127.0.0.1:7878），
只开放 `scrapling_get / fetch / stealthy_fetch` 三个只读工具给三省与六部。

---

## 架构

```
浏览器 ──SSE──▶ FastAPI (server/app.py, :9031)
                    │
                    ▼
              orchestrator.py（升朝 / 协议校验 / 纠正轮 / 收尾）
                    │  run_role()
                    ▼
         ┌──────────┴───────────┐
         │  kernel.py           │  每个角色一套工具 + 一次回合循环
         │  ├ 沙箱 read/write    │  ← workspace/<JJC-id>/
         │  ├ kanban_* 上报      │  ← 改 data/tasks.json
         │  └ call_agent 差遣    │  ← 按权限图，只允许存在的边
         └──────────┬───────────┘
                    ▼
        E:\Project\agent-core（AgentLoop / ToolRegistry / LoopGuard /
                              IterationBudget / EventBus / DeepSeek provider）
                    ▼
              DeepSeek api (deepseek-flash，流式)
                    │
                    └─ 联网：scrapling MCP（经 agent-core 的常驻网关，独立端口 7878）
```

内核**不复制**，直接以 `kernel.dir` 指向 `agent-core` 复用其回合循环（`finish_reason` 判据）、
工具契约、防循环守卫与事件流；本项目只写角色装配、沙箱策略与调度。

### 目录

```
edict-sim/
├── main.py                 入口（环境自举 + 起服务）
├── config.yaml             模型 / 预算 / 沙箱 / 端口
├── agents/                 十二官的提示词（GLOBAL → group → SOUL 三层）
│   ├── GLOBAL.md           全局朝规（铁律）
│   ├── groups/             sansheng.md（三省）· liubu.md（六部）
│   └── <role>/SOUL.md      单官职权与办差顺序
├── server/
│   ├── app.py              HTTP 面：颁旨 / 看板 / SSE / 产出预览
│   ├── orchestrator.py     调度中枢：升朝、协议校验、纠正轮、收尾
│   ├── kernel.py           角色装配：工具、沙箱、越权拦截、事件翻译
│   ├── task.py             状态机（逐条对齐 edict 的 models/task.py）
│   ├── roles.py            十二官定义 + 权限图 + 三层提示词拼装
│   ├── hub.py              事件中枢（SSE 源 + jsonl 落盘）
│   └── config.py           配置加载
├── web/                    零依赖前端：宣纸 / 墨 / 朱红
├── workspace/<JJC-id>/     沙箱：每道旨意一个目录（太子/中书省/门下省/尚书省/六部）
├── doc/                    导出默认落点（可在导出弹窗里改路径，已 gitignore）
├── data/tasks.json         看板存档
├── data/events/*.jsonl     事件流存档（可回放）
└── tests/selfcheck.py      离线自检
```

---

## 界面

四个面板自上而下：**信息总线 → 军机处看板 → 产出 → 十二官**，同一容器、同宽（1560px 上限 / 28px 边距），一眼对齐。

| 区域 | 说明 |
|---|---|
| **信息总线** | 在最上面。每条差遣 / 用印 / 动议 / 落笔 / 朱批实时流过，可按公文·用印·思虑·朱批筛选，点某个官只看他的往来。**向上翻着读时不会被新事件拽回底部**；底部中间浮出「↓ 到最新 N」按钮（N = 期间新增条数），点它才回到最新 |
| **军机处看板** | 九列（待理→太子→中书→门下→派发→执行→覆核→回奏 + 阻塞）**整幅铺开，九列全显**，不用横向拖动；窄屏自动折成两排。**点任一卡片即选中**（朱红描边 + 竖标），总线与产出同时切到那道旨意；卡片悬停右上角出现 **×** 可删除该旨意（办动中禁用，会说明原因） |
| **产出面板** | **只显示当前选中旨意**的产出（跟总线同源，不混在一起）；面板内按**职能目录分组**（`圣旨/ → 太子/ → 中书省/ → 门下省/ → 尚书省/ → 六部`，自上而下即流程层级）；点文件名按 markdown 排版阅读（正文 15.5px / 行高 1.95，表格真表格） |
| **原文可点开** | 每轮「圣旨 / 回奏」原文自动落成 `圣旨/圣旨原文·第N轮.md`、`圣旨/回奏·第N轮.md`；**总线里出现的任何沙箱 md 路径都是链接**，点开即读 |
| **导出** | 「导出本旨意」或「导出全部」；默认落到项目下 `doc/`，可**自己填任意绝对路径**；子目录名为 **`<旨意编号> <颁旨原文>`**（如 `JJC-20261018-005 我想一个月赚 100 万你们有什么建议/`），系统目录被拒 |

---

## 删除旨意

看板卡片右上角的 **×** → 确认弹窗会写清楚：

- 哪一道旨意、当前状态（含轮次）、沙箱里有几份文书
- 删除后**看板卡片、产出清单、总线回放都会消失，不可恢复**
- 勾选框「同时删除沙箱目录（N 份文书）」默认勾上；取消勾选则只从看板移除，文件留在 `workspace/<编号>/` 里（界面不再显示）
- 正在办（running）的旨意不能删，按钮禁用并提示先「叫停」

删除动作记在 `data/deleted.jsonl`（时间 / 编号 / 标题 / 状态 / 份数），删掉的东西至少留一条账。

---

## 联网：让六部真去查

不再只凭模型记忆办事。每个角色装配工具时，如果自己开了联网，就多出三个**只读**工具：

| 工具 | 用途 |
|---|---|
| `scrapling_get(url)` | 静态页首选，最快 |
| `scrapling_fetch(url)` | 需要 JS 渲染的页 |
| `scrapling_stealthy_fetch(url)` | 被反爬拦了才用（会开隐身浏览器） |

- **谁能用**：中书省、门下省、尚书省、六部（吏户礼兵刑工）开；太子只分拣，旁观官不开
- **后端**：复用 [agent-core](../agent-core) 的 MCP 客户端 + 常驻网关（`tools/mcp_gateway.py`），
  网关跑在 `E:\Project\Scrapling-main\.venv` 里，端口 **7878**（独立于 airi-lite 的 7877），服务重启会 `/health` 探测复用，不重复拉浏览器
- **长度纪律**：网页正文一律截到 6000 字再喂模型（`mcp.max_chars`），省 token 也防跑题
- **失败不拦朝会**：网关没起来就当没有联网工具，提示词会退化成「按已知信息办，注明未联网核实」
- **日期注入**：运行时情报里带「今日：2026-10-18」，避免模型拿旧记忆当最新

实测（本机 2026-10-18）：旨意「查一下 DeepSeek 现在的模型与 API 定价」→ 中书与门下各抓官方定价页，
兵部/户部据此产出《定价核验》《成本估算》，正文每个数值后都标了
`https://api-docs.deepseek.com/zh-cn/quick_start/pricing`，跑完 106 次调用、12 份文书、回奏 Done。

---

## 回奏：在原旨意上再走一轮

「颁旨」是开一条新线；「回奏」不是。

```
第一批：皇上 → 太子 → 中书 → 门下 → 尚书 → 六部 → 回奏(Done)
                                        │
                      陛下看完，觉得还要补 │  ← 点「回 奏」，写清本轮圣意
                                        ▼
第二批：      太子 → 中书 → 门下 → 尚书 → 六部 → 回奏(Done)   （同一道旨意，第 2 轮）
```

- **原文留档可查**：回奏弹窗顶部列出「原文 · 点开看全文」——第 1 轮圣旨 + 历次回奏，点任意一条即按 markdown 打开原文（自动落在 `圣旨/` 目录，导出时一并带走）
- **不新建看板卡片**：还是原来那张卡，只在编号后挂一个 `第N轮` 角标，状态从 Done 退回 太子 重新往下走
- **产出叠加不覆盖**：本轮文书一律带 `·第N轮` 后缀（`中书省/方案·第2轮.md`、`门下省/门下审议意见·第2轮.md`…），上一轮定稿原样留着
- **状态机仍然把关**：`Done → Taizi` 只能由皇上的回奏接口触发；Agent 自己调 `kanban_state` 从终态推状态照样被拒
- **预算按轮重算**：每轮各自有 `max_llm_calls` 额度（默认 120），累计调用数照旧记账
- **边界**：正在办（running）的旨意不能回奏，得等它出结果；封驳轮次 `rejects` 也随新轮清零

入口：选中一道已回奏的旨意 → 产出面板右上「回 奏」→ 填本轮圣意 → 下旨续办。

---

## 沙箱：两道锁

1. **物理边界**：任何 `read_file` / `write_file` / `list_dir` 的路径都会 resolve，
   必须落在 `workspace/<JJC-id>/` 内，`../`、绝对路径、符号链接逃逸一律拒绝。
2. **职能边界**：读可以读遍本旨意目录（看同僚产出），**写只允许自己职能目录**：

   | 角色 | 可写目录 |
   |---|---|
   | 太子 / 中书省 / 门下省 / 尚书省 | `太子/` `中书省/` `门下省/` `尚书省/` |
   | 六部 | `户部/` `礼部/` `兵部/` `刑部/` `工部/` `吏部/` |

   目录名就是职能名，所以产出面板里的前缀一眼能看出是谁交的。
   越界写入被拒 + 记审计；六部还没有 `kanban_state` 权限，改不了流程状态。

```python
# server/kernel.py
allowed = _write_dir(task.id, r)               # 该角色唯一可写目录
if s.dept_write_only and target != allowed and allowed not in target.parents:
    return _deny(task, r, "write_file", f"{r.label} 只能写 {r.dept_dir}/ ...")
```

---

## 与 edict 的对齐 / 差异

| | edict（原项目） | 本项目 |
|---|---|---|
| 角色协作 | `openclaw agent` 子进程派发，角色间靠看板状态触发 | `call_agent` 工具**嵌套跑同一回合循环**，是真实调用栈 |
| 共享状态 | `data/tasks_source.json` + `kanban_update.py` CLI + 文件锁 | `data/tasks.json` + `kanban_*` 工具（同一套状态机与命令白名单） |
| 状态机 | `edict/backend/app/models/task.py:STATE_TRANSITIONS` | `server/task.py` 逐条复制（含 `Blocked` 回退、三轮封驳上限） |
| 权限图 | `agents.json` 的 `subagents.allowAgents` | `server/roles.py:ROLES[*].allows` |
| 命令白名单 | `kanban_update.py:AGENT_POLICY` | `ROLES[*].policy`（六部无 `kanban_state/flow`） |
| 审核 | 门下省是必经关卡 | 同；并且**准奏后由门下自己转交尚书**（避免重复派发） |
| 韧性 | 停滞扫描 → 重试 → 升级 → 回滚 → Blocked | 协议校验 + 纠正轮（换人救场）+ 模型调用硬顶 → 未回奏如实标 Blocked |
| 引擎 | 无（靠外部 agent runtime） | `E:\Project\agent-core`（回合循环 / 工具契约 / 防循环 / 预算） |
| 续办 | 无（一道旨意一次办完） | **回奏**：同一旨意上续轮，Done 退回太子重走流程，产出带轮次后缀 |

刻意的取舍：**不做故障注入按钮**。真实模型的不守规矩（跳状态、写错目录、跳过审核）本身就是最好的演示素材，
系统把它们拦下来并记进审计流，比人为造的假故障更有说服力。

---

## 配置（config.yaml）

```yaml
provider:
  model: deepseek-flash     # 换 deepseek-v4-pro 文风更好、更贵
mcp:
  enabled: true             # 关掉就纯离线（工具不装配，提示词自动降级）
  gateway_url: http://127.0.0.1:7878
  max_chars: 6000           # 网页正文截断
  web_tools: [scrapling_get, scrapling_fetch, scrapling_stealthy_fetch]
budget:
  max_rounds_per_role: 5    # 基线轮数（各官实际轮数见 roles.Role.rounds）
  max_llm_calls: 120        # 一道旨意的模型调用硬顶
  max_depth: 4              # call_agent 嵌套深度上限
sandbox:
  root: workspace           # 沙箱根（相对项目根）
  dept_write_only: true     # 部门写边界
```

各官轮数预算：太子 9 / 中书 8 / 门下 9 / 尚书 18 / 六部 8（尚书要连着派发多部，所以给得多）。

---

## 清空历史

看板与事件流都是本地文件，想从干净状态重新演示：

```bash
rm -rf data/tasks.json data/events workspace/JJC-*
```
（`workspace/` 与 `data/` 已在 .gitignore 里，不会入库。）

---

## 隐私与数据边界

这个项目只在**本机**跑，对外只发一种请求：发给模型 API（默认 `api.deepseek.com`）。

- **写**：只会落在项目内的 `workspace/<旨意编号>/`（沙箱）与 `data/`（看板、事件流）。路径 resolve 后越界一律拒绝
- **读**：模型只能读本旨意沙箱；开了联网才会去抓你/模型给出的 URL（抓到的正文会截断后进上下文）
- **密钥**：只放在 `.env`（已 gitignore），不上传、不写日志、不进看板
- **导出**：只有你点「导出」才会把沙箱复制到你指定的目录
- **删数据**：只有你点卡片上的 × 才会删，且弹窗会写清删哪些、几份、是否连沙箱一起删
- 没有任何遥测、没有远程上报、没有后台常驻任务

---

## 许可

[MIT](LICENSE)。拿去改、拿去商用都行，保留版权声明即可。

---

## 已知限制

- **时长**：一场朝会 4–8 分钟（取决于中书拆几路）。界面上有实时流，不是黑盒等待。
- **模型不总守流程**：会跳状态、会忘记交差，靠协议校验 + 纠正轮兜底（最多 3 轮），兜不住就如实标 Blocked，不伪造 Done。
- **单进程内存态**：正在跑的朝会保存在内存，重启会中断（已完成的旨意与事件流都已落盘，可回放）。
- **没有鉴权**：只监听 `127.0.0.1`，本机自用。
- 六部之间的横向沟通被刻意切断（没有 `call_agent` 权限），与原项目的 `sessions_send` 语义不同——这里用"回话即返回"代替。

---

## 自检

```bash
start.bat test
```

覆盖：状态机合法/非法跳转、沙箱路径逃逸（`../`、绝对路径、Windows 盘符）、
权限图（六部无改状态权、太子只能差遣中书、尚书可派六部、门下可回调中书）、
十二官提示词三层拼装完整性。

上次结果：**38 项全部 PASS**（含回奏续轮的 8 项、联网授权的 2 项）。
