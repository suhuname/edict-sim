"""联网桥 —— 把 scrapling MCP 的「读网页」能力接到朝堂上。

复用 agent-core 现成的 MCP 客户端（`agent.mcp.McpClient`）：它会在需要时 detached 拉起
`tools/mcp_gateway.py` 常驻网关（跑在 Scrapling 的 venv 里），我们这里只管挑工具、限长度、报状态。

三条纪律：
1. **失败不拦朝会**：网关没起来就当作没有联网工具，六部照样用手上的东西干活。
2. **结果必须裁剪**：一个网页动辄几万字，全塞进上下文既贵又糊；统一截到 N 字并注明。
3. **只暴露读**：只放 get / fetch / stealthy_fetch 三个读取类工具，不给会话、截图那些重家伙。
"""
from __future__ import annotations

import os
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Any

from .config import ROOT, get_settings

_S = get_settings()
if str(_S.kernel_dir) not in sys.path:
    sys.path.insert(0, str(_S.kernel_dir))

from agent.config import McpConfig, McpServerConfig  # noqa: E402
from agent.mcp import McpClient, McpToolDef  # noqa: E402


class WebBridge:
    """一个进程一份的 MCP 客户端封装（线程安全地懒启动）。"""

    def __init__(self) -> None:
        self._client: McpClient | None = None
        self._lock = threading.Lock()
        self._started = False
        self._last_error = ""
        self._tools: dict[str, McpToolDef] = {}

    # ---------- 生命周期 ----------

    def _build_client(self) -> McpClient:
        s = get_settings()
        def _command(sv: dict[str, Any]) -> str:
            raw = os.environ.get("EDICT_SCRAPLING") or str(sv.get("command") or "")
            if raw and not Path(raw).is_file():
                alt = shutil.which("scrapling") or shutil.which("scrapling.exe")
                if alt:
                    return alt
            return raw

        servers = [
            McpServerConfig(
                name=str(sv.get("name") or "server"),
                description=str(sv.get("description") or ""),
                command=_command(sv),
                args=[str(a) for a in (sv.get("args") or [])],
                enabled=bool(sv.get("enabled", True)),
                timeout_ms=int(sv.get("timeout_ms") or s.mcp_call_timeout),
            )
            for sv in s.mcp_servers
        ]
        cfg = McpConfig(
            enabled=s.mcp_enabled and bool(servers),
            autostart=True,
            gateway_url=s.mcp_gateway_url,
            python=str(s.mcp_python),
            script=s.mcp_script,
            log=s.mcp_log,
            ready_timeout_s=s.mcp_ready_timeout,
            tools_ready_timeout_s=s.mcp_tools_timeout,
            call_timeout_ms=s.mcp_call_timeout,
            servers=servers,
        )
        return McpClient(cfg, root=ROOT)

    def start(self, blocking: bool = False) -> None:
        """起网关并握手。blocking=False 时丢到后台线程，不拖慢服务启动。"""
        if self._started:
            return
        with self._lock:
            if self._started:
                return
            self._started = True

        def _run() -> None:
            t0 = time.time()
            try:
                client = self._build_client()
                ok = client.start()
                self._client = client
                if not ok:
                    self._last_error = client.error or "未知错误"
                else:
                    self._tools = {t.name: t for t in client.tools()}
                    self._last_error = ""
                print(f"[web] scrapling MCP {'就绪' if ok else '不可用'} "
                      f"({time.time() - t0:.1f}s, {len(self._tools)} 个工具)"
                      + ("" if ok else f"：{self._last_error}"), flush=True)
            except Exception as exc:  # noqa: BLE001 — 联网挂了不该拦住朝会
                self._last_error = f"{type(exc).__name__}: {exc}"
                print(f"[web] scrapling MCP 启动失败：{self._last_error}", flush=True)

        if blocking:
            _run()
        else:
            threading.Thread(target=_run, name="mcp-boot", daemon=True).start()

    # ---------- 工具 ----------

    @property
    def available(self) -> bool:
        return bool(self._tools)

    def tools(self) -> dict[str, McpToolDef]:
        """只给出配置里白名单的读取类工具。"""
        s = get_settings()
        allow = set(s.web_tools)
        return {k: v for k, v in self._tools.items() if (not allow or k in allow)}

    def call(self, name: str, args: dict[str, Any]) -> str:
        s = get_settings()
        if self._client is None:
            return "（联网工具还没连上，先按已知信息办）"
        out = self._client.call(name, args)
        text = out.text if hasattr(out, "text") else str(out)
        limit = max(500, int(s.web_max_chars))
        if len(text) > limit:
            text = text[:limit] + f"\n…（原文过长，已截断；共 {len(text)} 字）"
        return text

    def status(self) -> dict[str, Any]:
        rows = self._client.status() if self._client else []
        return {
            "enabled": get_settings().mcp_enabled,
            "available": self.available,
            "tools": sorted(self.tools().keys()),
            "servers": rows,
            "error": self._last_error,
        }


web = WebBridge()
