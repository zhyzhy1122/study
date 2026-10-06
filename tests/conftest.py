# -*- coding: utf-8 -*-
"""
tests/conftest.py
测试共享配置与夹具

设计原则（三条硬约束）：
  1. 完全离线：任何 LLM / MCP / 网络调用都被替换成假实现（见 fake_llm 等夹具）。
  2. 完全隔离：项目里 src/** 把数据库和导出路径写死在 data/ 下，这里统一
     把模块级路径常量重定向到 tmp_path，保证测试不碰 data/*.db、data/workspace.json。
  3. 进程内干净：ToolRegistry / AGENT_REGISTRY / init_tools 都是模块级单例，
     每个用例前重置，避免用例间互相污染。

注意：conftest 只重定向"运行期"路径，不修改任何 src 代码。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# 项目根目录：测试无论从哪个 cwd 启动，都能 import src.*
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def pytest_configure(config):
    """确保项目根在 sys.path 上（pytest 的 rootdir/conftest 机制通常已保证）。"""
    import sys

    root = str(PROJECT_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def run_async(coro):
    """
    在同步测试里跑一个协程。

    不引入 pytest-asyncio：项目 venv 里没有它，pytest 9 会直接报"async def
    functions are not supported"。每个异步用例自己开一个 event loop 反而
    更可控（互不共享 loop，避免 aiosqlite 连接跨 loop 复用的问题）。
    """
    return asyncio.run(coro)


@pytest.fixture
def asyncio_run():
    """把 run_async 作为夹具暴露，让用例读起来更自然。"""
    return run_async


# ---------------------------------------------------------------------------
# 假 LLM：所有"不让测试联网"的替身都从这里出
# ---------------------------------------------------------------------------

class FakeMessage:
    """最小化的"模型返回消息"：只保留被业务代码读取的 .content。"""

    def __init__(self, content: str):
        self.content = content


class FakeLLM:
    """
    可脚本化的假模型，同时提供同步 invoke 与异步 ainvoke。

    参数:
        responses: 依次返回的文本内容；用完后重复最后一条。
        on_call:   可选回调 (prompt) -> None，用来断言"提示词里有什么"。
    """

    def __init__(self, responses, on_call=None):
        if isinstance(responses, str):
            responses = [responses]
        self.responses = list(responses)
        self.calls: list[str] = []
        self.on_call = on_call

    # -- 内部：记录 + 取下一个回复 --
    def _next(self, prompt) -> str:
        text = prompt if isinstance(prompt, str) else str(prompt)
        self.calls.append(text)
        if self.on_call is not None:
            self.on_call(text)
        if not self.responses:
            return ""
        if len(self.responses) == 1:
            return self.responses[0]
        return self.responses.pop(0)

    # -- 同步 --
    def invoke(self, prompt):
        return FakeMessage(self._next(prompt))

    # -- 异步 --
    async def ainvoke(self, prompt):
        return self._next(prompt)


@pytest.fixture
def fake_llm_factory():
    """返回 FakeLLM 类，方便用例按需构造。"""
    return FakeLLM


@pytest.fixture
def fake_llm():
    """默认假模型：内容固定为一段合法 JSON（记忆中间件用得到）。"""
    return FakeLLM(json.dumps({"should_update": False}, ensure_ascii=False))


# ---------------------------------------------------------------------------
# 全套路径隔离（autouse）：任何用例都不得写到 tests/ 之外
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_project_paths(tmp_path, monkeypatch):
    """
    把 src 里所有"运行期写盘路径"重定向到 tmp_path。

    涉及三处：
      - src/memory/messages.py  : MESSAGES_DB_PATH（对话原文库）
      - src/memory/store.py     : MEMORY_DB_PATH（长期记忆库）
      - src/api/routes/chat.py  : 诊断日志 / 上传目录 / 对话记录库 / 导出目录
      - src/api/routes/workspace.py : workspace.json + 默认导出目录

    这些路径在 src 里是模块级常量，在函数体内被读取，因此
    monkeypatch.setattr(模块, "常量名", 新值) 就能生效，且用例结束自动还原。
    """
    import importlib

    messages_mod = importlib.import_module("src.memory.messages")
    store_mod = importlib.import_module("src.memory.store")
    chat_mod = importlib.import_module("src.api.routes.chat")
    workspace_mod = importlib.import_module("src.api.routes.workspace")

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    # --- 短期对话原文库 ---
    monkeypatch.setattr(messages_mod, "DATA_DIR", data_dir, raising=True)
    monkeypatch.setattr(messages_mod, "MESSAGES_DB_PATH", data_dir / "messages.db", raising=True)

    # --- 长期记忆库 ---
    monkeypatch.setattr(store_mod, "MEMORY_DIR", data_dir, raising=True)
    monkeypatch.setattr(store_mod, "MEMORY_DB_PATH", data_dir / "memory.db", raising=True)

    # --- chat 路由的运行期文件 ---
    monkeypatch.setattr(chat_mod, "_LOG_FILE", data_dir / "debug_chat.log", raising=False)
    monkeypatch.setattr(chat_mod, "_CONV_DB_PATH", data_dir / "conversations.db", raising=False)
    monkeypatch.setattr(chat_mod, "_UPLOAD_DIR", data_dir / "uploads", raising=False)
    monkeypatch.setattr(chat_mod, "_EXPORT_DIR_DEFAULT", data_dir / "exports", raising=False)
    monkeypatch.setattr(
        chat_mod, "_PATH_EXPORT_DIR_DEFAULT", data_dir / "exports", raising=False
    )

    # --- workspace 路由的持久化文件 ---
    monkeypatch.setattr(workspace_mod, "_WORKSPACE_FILE", data_dir / "workspace.json", raising=True)
    monkeypatch.setattr(
        workspace_mod, "_DEFAULT_EXPORT_DIR", data_dir / "exports", raising=True
    )
    monkeypatch.setattr(workspace_mod, "_current", None, raising=True)

    return data_dir


@pytest.fixture(autouse=True)
def reset_global_singletons(monkeypatch):
    """
    重置模块级单例，保证用例互不污染：
      - ToolRegistry 单例 + init_tools 的初始化标记
      - BaseAgent 的 AGENT_REGISTRY（懒加载 Agent 缓存）
    """
    import importlib

    # 注意：这里用 importlib 而不是 `import x as y`。
    # pytest 会把模块级名字当夹具解析，形如 `init_tools_mod = import src....`
    # 的写法会被误判成 fixture 覆盖，所以统一用 importlib 动态取模块。
    registry_mod = importlib.import_module("src.modules.tools.registry")
    tools_init_mod = importlib.import_module("src.modules.tools.init_tools")
    agents_base_mod = importlib.import_module("src.agents.base")

    monkeypatch.setattr(registry_mod, "_tool_registry_instance", None, raising=True)
    monkeypatch.setattr(tools_init_mod, "_initialized", False, raising=True)
    monkeypatch.setattr(tools_init_mod, "_mcp_initialized", False, raising=True)
    monkeypatch.setattr(agents_base_mod, "AGENT_REGISTRY", {}, raising=True)
    yield


@pytest.fixture(autouse=True)
def force_no_mcp_preload(monkeypatch):
    """
    禁止 FastAPI lifespan 真的去连 MCP（否则会 npx 起子进程 / 连网络）。
    只把 deps 命名空间里的引用换掉，不影响 init_tools 自身逻辑。
    """
    import src.api.deps as deps

    async def _noop_mcp() -> int:
        return 0

    monkeypatch.setattr(deps, "init_mcp_tools", _noop_mcp, raising=True)
    yield


# ---------------------------------------------------------------------------
# 假 LLM 工厂的"安装器"：按模块替换 get_deepseek_llm
# ---------------------------------------------------------------------------

@pytest.fixture
def patch_llm(monkeypatch):
    """
    返回一个安装函数：patch_llm(module, responses) —— 把假模型装上，并回传它供断言。

    用法:
        llm = patch_llm(reflection_mw, [judge_json])     # module 顶层 import 的工厂
        llm = patch_llm(None, [payload])                 # 延迟 import 的场景，见下

    module 传 None 时，替换的是"工厂函数的定义处" src.utils.llm.get_deepseek_llm。
    有些模块（如 memory_middleware）是在函数体内 `from src.utils.llm import
    get_deepseek_llm`，局部名字取的是定义处的最新值，因此只能从源头打补丁。
    """
    installed = []

    def _install(module, responses, on_call=None):
        fake = FakeLLM(responses, on_call=on_call)
        if module is None:
            import src.utils.llm as llm_mod

            target = llm_mod
        else:
            target = module
        monkeypatch.setattr(target, "get_deepseek_llm", lambda *a, **k: fake, raising=True)
        installed.append(fake)
        return fake

    return _install


# ---------------------------------------------------------------------------
# FastAPI TestClient 夹具
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client():
    """
    带 lifespan 的 TestClient（触发启动/关闭流程，但不连 MCP）。
    退出时正确关闭，避免事件循环泄漏。
    """
    from fastapi.testclient import TestClient
    from src.api.app import app

    with TestClient(app) as client:
        yield client


@pytest.fixture
def api_client_no_lifespan():
    """
    不带 lifespan 的 TestClient：用于只关心路由/校验、不关心启动流程的用例。
    """
    from fastapi.testclient import TestClient
    from src.api.app import app

    return TestClient(app)
