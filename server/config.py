"""配置加载 —— 配置驱动，换模型/换路径/调预算都不改代码。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _load_env(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


@dataclass
class Settings:
    host: str = "127.0.0.1"
    port: int = 9031
    kernel_dir: Path = field(default_factory=lambda: ROOT.parent / "agent-core")
    base_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-flash"
    api_key_env: str = "DEEPSEEK_API_KEY"
    temperature: float = 0.7
    timeout_s: float = 120.0
    max_rounds: int = 7
    max_llm_calls: int = 60
    max_depth: int = 4
    stall_timeout_s: int = 180
    sandbox_root: Path = field(default_factory=lambda: ROOT / "workspace")
    data_root: Path = field(default_factory=lambda: ROOT / "data")
    agents_dir: Path = field(default_factory=lambda: ROOT / "agents")
    web_dir: Path = field(default_factory=lambda: ROOT / "web")
    dept_write_only: bool = True
    max_write_bytes: int = 200_000
    max_read_bytes: int = 60_000
    # 联网（scrapling MCP 网关）
    mcp_enabled: bool = True
    mcp_gateway_url: str = "http://127.0.0.1:7878"
    mcp_python: Path = field(default_factory=lambda: ROOT.parent / "Scrapling-main" / ".venv" / "Scripts" / "python.exe")
    mcp_script: Path = field(default_factory=lambda: ROOT.parent / "agent-core" / "tools" / "mcp_gateway.py")
    mcp_log: Path = field(default_factory=lambda: ROOT / "data" / "mcp_gateway.log")
    mcp_ready_timeout: float = 30.0
    mcp_tools_timeout: float = 30.0
    mcp_call_timeout: int = 60_000
    mcp_servers: list[dict[str, Any]] = field(default_factory=list)
    web_tools: list[str] = field(default_factory=list)
    web_max_chars: int = 6000


def _resolve_path(value: str | Path | None, default: Path, env: str = "") -> Path:
    """环境变量 → 配置值 → 默认值；相对路径一律按项目根解析（仓库可搬走）。"""
    raw = (os.environ.get(env) if env else None) or value or default
    p = Path(raw).expanduser()
    return p if p.is_absolute() else (ROOT / p)


def _pick(raw: dict[str, Any], *keys: str, default: Any = None) -> Any:
    cur: Any = raw
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur if cur is not None else default


_settings: Settings | None = None


def get_settings(reload: bool = False) -> Settings:
    global _settings
    if _settings is not None and not reload:
        return _settings

    _load_env(ROOT / ".env")
    raw: dict[str, Any] = {}
    cfg = ROOT / "config.yaml"
    if cfg.is_file():
        raw = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}

    s = Settings(
        host=_pick(raw, "server", "host", default="127.0.0.1"),
        port=int(_pick(raw, "server", "port", default=9031)),
        kernel_dir=_resolve_path(_pick(raw, "kernel", "dir"),
                                ROOT.parent / "agent-core", env="EDICT_KERNEL_DIR"),
        base_url=_pick(raw, "provider", "base_url", default="https://api.deepseek.com/v1"),
        model=_pick(raw, "provider", "model", default="deepseek-flash"),
        api_key_env=_pick(raw, "provider", "api_key_env", default="DEEPSEEK_API_KEY"),
        temperature=float(_pick(raw, "provider", "temperature", default=0.7)),
        timeout_s=float(_pick(raw, "provider", "timeout_s", default=120)),
        max_rounds=int(_pick(raw, "budget", "max_rounds_per_role", default=7)),
        max_llm_calls=int(_pick(raw, "budget", "max_llm_calls", default=60)),
        max_depth=int(_pick(raw, "budget", "max_depth", default=4)),
        stall_timeout_s=int(_pick(raw, "budget", "stall_timeout_s", default=180)),
        dept_write_only=bool(_pick(raw, "sandbox", "dept_write_only", default=True)),
        max_write_bytes=int(_pick(raw, "sandbox", "max_write_bytes", default=200_000)),
        max_read_bytes=int(_pick(raw, "sandbox", "max_read_bytes", default=60_000)),
    )
    mcp_raw: dict[str, Any] = _pick(raw, "mcp", default={}) or {}
    s.mcp_enabled = bool(mcp_raw.get("enabled", True))
    s.mcp_gateway_url = str(mcp_raw.get("gateway_url") or "http://127.0.0.1:7878")
    s.mcp_python = _resolve_path(mcp_raw.get("python"), s.mcp_python, env="EDICT_MCP_PYTHON")
    s.mcp_script = _resolve_path(mcp_raw.get("script"), s.mcp_script, env="EDICT_MCP_SCRIPT")
    s.mcp_ready_timeout = float(mcp_raw.get("ready_timeout_s") or 30)
    s.mcp_tools_timeout = float(mcp_raw.get("tools_ready_timeout_s") or 30)
    s.mcp_call_timeout = int(mcp_raw.get("call_timeout_ms") or 60_000)
    s.mcp_servers = list(mcp_raw.get("servers") or [])
    # 服务器命令里的相对路径按项目根解析成绝对路径（网关的 cwd 不在项目根，必须绝对）
    for sv in s.mcp_servers:
        cmd = sv.get("command")
        if cmd and not Path(str(cmd)).is_absolute():
            sv["command"] = str((ROOT / str(cmd)).resolve())
    s.web_tools = list(mcp_raw.get("web_tools") or [])
    s.web_max_chars = int(mcp_raw.get("max_chars") or 6000)
    log_path = Path(mcp_raw.get("log") or "data/mcp_gateway.log")
    s.mcp_log = log_path if log_path.is_absolute() else (ROOT / log_path)

    root = Path(_pick(raw, "sandbox", "root", default="workspace"))
    s.sandbox_root = root if root.is_absolute() else (ROOT / root)
    s.sandbox_root.mkdir(parents=True, exist_ok=True)
    s.data_root.mkdir(parents=True, exist_ok=True)
    _settings = s
    return s
