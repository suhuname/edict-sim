#!/usr/bin/env python
"""三省六部 · 朝堂信息流模拟器 —— 统一入口。

    python main.py            启动（浏览器打开 http://127.0.0.1:9031）
    python main.py test       跑离线自检（不调模型）
    python main.py -h         看全部参数

**环境自举**：如果项目同级有 `.venv`（或设置了 `EDICT_PYTHON`），本脚本会自动切过去重跑，
不用 activate、不用记 venv 路径；都没有就原地运行。Windows 上双击 `start.bat` 完全等价。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT = "edict-sim"
_REEXEC_FLAG = "_PROJECT_REEXEC"

ROOT = Path(__file__).resolve().parent
TAG = f"[{PROJECT}]"


def _shared_python() -> Path | None:
    """找一个「带依赖的 python」：环境变量 → 项目同级的 .venv → 没有就原地跑。
    这样仓库搬到别的机器上不会因为写死的路径而跑不起来。"""
    cands = [os.environ.get("EDICT_PYTHON")]
    cands += [
        str(ROOT.parent / ".venv" / "Scripts" / "python.exe"),   # Windows 同级 venv
        str(ROOT.parent / ".venv" / "bin" / "python"),           # POSIX 同级 venv
    ]
    for c in cands:
        if c and Path(c).is_file():
            return Path(c)
    return None


def bootstrap() -> None:
    """确保跑在通用环境里。不在就切过去重跑一次（只切一次，防递归）。"""
    if os.environ.get(_REEXEC_FLAG) == "1":
        return
    GLOBAL_PY = _shared_python()
    if GLOBAL_PY is None:
        return
    try:
        already = Path(sys.executable).resolve() == GLOBAL_PY.resolve()
    except OSError:
        already = False
    if already:
        return

    # 清 PYTHONPATH：外部（如 Hermes）注入的路径会污染 venv
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env[_REEXEC_FLAG] = "1"
    print(f"{TAG} 当前解释器不是通用环境，自动切换：{GLOBAL_PY}")
    raise SystemExit(subprocess.call([str(GLOBAL_PY), str(ROOT / "main.py"), *sys.argv[1:]], env=env))


bootstrap()

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_dotenv() -> None:
    """极简 .env 读取 —— 不引 python-dotenv。已有环境变量优先。"""
    f = ROOT / ".env"
    if not f.is_file():
        return
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    _load_dotenv()

    if argv and argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0

    if argv and argv[0] == "test":
        sys.path.insert(0, str(ROOT))
        from tests.selfcheck import main as _test  # noqa: PLC0415

        return _test()

    # 默认：起服务
    if str(ROOT / "server") not in sys.path:
        sys.path.insert(0, str(ROOT))
    import uvicorn  # noqa: PLC0415

    from server.config import get_settings  # noqa: PLC0415

    s = get_settings()
    if not s.kernel_dir.is_dir():
        print(f"{TAG} 找不到内核目录：{s.kernel_dir}")
        print(f"{TAG} 本项目的回合循环/工具契约借自 agent-core，请二选一：")
        print(f"{TAG}   1) 把 agent-core 放在项目同级目录（默认 ../agent-core）")
        print(f"{TAG}   2) 设置环境变量 EDICT_KERNEL_DIR，或改 config.yaml 的 kernel.dir")
        return 2
    print(f"{TAG} 三省六部 · 朝堂信息流模拟器")
    print(f"{TAG} 神机内核: {s.kernel_dir}")
    print(f"{TAG} 模型: {s.model}  (调用上限 {s.max_llm_calls} 次/旨意)")
    print(f"{TAG} 沙箱: {s.sandbox_root}")
    print(f"{TAG} 联网: scrapling MCP @ {s.mcp_gateway_url}（{'启用' if s.mcp_enabled else '关闭'}）")
    print(f"{TAG} 打开 http://{s.host}:{s.port}")
    uvicorn.run("server.app:app", host=s.host, port=s.port, log_level="warning", reload=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
