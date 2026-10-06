# -*- coding: utf-8 -*-
"""
tests/test_tools_registry.py
工具注册中心测试（src/modules/tools/registry.py + init_tools.py）

覆盖重点：
  - 分组注册：新组覆盖、同组追加、外部列表不可被反向污染
  - 查询：按组取、取全部（顺序）、列组名、未知组返回空列表
  - MCP 注册：server 名与分组名的映射关系
  - 单例语义：get_tool_registry() 多次调用返回同一实例
  - init_tools() 的幂等性（不会重复注册工具）
无网络：全部用假的 BaseTool 对象，不触发任何 MCP / HTTP。
"""

from __future__ import annotations

import importlib

import pytest
from langchain_core.tools import StructuredTool

from src.modules.tools.agent_tools import build_learning_path_tool
from src.modules.tools.registry import ToolRegistry, get_tool_registry

# 注意：包 __init__ 里把 init_tools 这个"函数"重名导出，会遮蔽同名子模块，
# 所以用 importlib 取真正的模块对象（模块里还有 _initialized 等状态需要打桩）。
tools_init = importlib.import_module("src.modules.tools.init_tools")


def make_tool(name: str) -> StructuredTool:
    """构造一个最小可用工具（真实 StructuredTool，不是 Mock）。"""
    return StructuredTool.from_function(
        func=lambda: f"{name}-result",
        name=name,
        description=f"测试用工具 {name}",
    )


# ===========================================================================
# 注册
# ===========================================================================

def test_register_group_creates_new_group():
    registry = ToolRegistry()
    a, b = make_tool("a"), make_tool("b")

    registry.register_group("search", [a, b])

    assert registry.list_groups() == ["search"]
    assert registry.get_tools("search") == [a, b]


def test_register_group_appends_when_group_exists():
    registry = ToolRegistry()
    a, b = make_tool("a"), make_tool("b")

    registry.register_group("search", [a])
    registry.register_group("search", [b])

    assert [t.name for t in registry.get_tools("search")] == ["a", "b"]


def test_register_group_copies_input_list():
    """外部列表之后被清空，不应影响注册中心内部状态。"""
    registry = ToolRegistry()
    incoming = [make_tool("a")]

    registry.register_group("search", incoming)
    incoming.clear()

    assert len(registry.get_tools("search")) == 1


def test_register_single_tool_goes_into_group():
    registry = ToolRegistry()
    tool = make_tool("solo")

    registry.register_tool("misc", tool)

    assert registry.get_tools("misc") == [tool]


# ===========================================================================
# 查询
# ===========================================================================

def test_get_tools_returns_empty_list_for_unknown_group():
    assert ToolRegistry().get_tools("不存在") == []


def test_get_all_tools_concatenates_every_group_in_registration_order():
    registry = ToolRegistry()
    a, b, c = make_tool("a"), make_tool("b"), make_tool("c")

    registry.register_group("first", [a, b])
    registry.register_group("second", [c])

    assert [t.name for t in registry.get_all_tools()] == ["a", "b", "c"]


def test_get_all_tools_is_empty_on_fresh_registry():
    assert ToolRegistry().get_all_tools() == []
    assert ToolRegistry().list_groups() == []


def test_list_mcp_servers_tracks_registered_servers():
    registry = ToolRegistry()
    registry.register_mcp_server("tavily", [make_tool("tavily_search")])
    registry.register_mcp_server("playwright", [make_tool("browser_navigate")])

    assert registry.list_mcp_servers() == ["tavily", "playwright"]
    # 同时注册到分组系统，上层可以按组拿
    assert registry.list_groups() == ["tavily", "playwright"]


def test_register_mcp_server_allows_custom_group_name():
    registry = ToolRegistry()
    tools = [make_tool("tavily_search")]

    registry.register_mcp_server("tavily", tools, group_name="mcp_search")

    assert "tavily" in registry.list_mcp_servers()
    assert registry.get_tools("mcp_search") == tools
    assert registry.get_tools("tavily") == []  # 用自定义组名时不再落到 server 名组


def test_registry_instances_are_independent():
    first = ToolRegistry()
    second = ToolRegistry()

    first.register_group("g", [make_tool("a")])

    assert second.list_groups() == []


# ===========================================================================
# 单例
# ===========================================================================

def test_get_tool_registry_returns_the_same_instance():
    one = get_tool_registry()
    two = get_tool_registry()

    assert isinstance(one, ToolRegistry)
    assert one is two


def test_get_tool_registry_singleton_persists_registrations():
    registry = get_tool_registry()
    registry.register_group("only_once", [make_tool("a")])

    # 再次获取同一个单例，注册内容还在（说明不是每次新建）
    assert get_tool_registry().get_tools("only_once")


# ===========================================================================
# init_tools：真实注册流程 + 幂等
# ===========================================================================

def test_init_tools_registers_sub_agents_group(monkeypatch):
    # 显式声明"配了可选 Key"，避免用例结论随本机 .env 变化
    from src.config import settings

    monkeypatch.setattr(settings, "tavily_api_key", "tvly-fake", raising=False)
    monkeypatch.setattr(settings, "dashscope_api_key", "dash-fake", raising=False)

    tools_init.init_tools()

    registry = get_tool_registry()
    names = [t.name for t in registry.get_tools("sub_agents")]

    assert names == [
        "learning_path_expert",
        "code_review_expert",
        "search_expert",
        "multimodal_expert",
    ]


def test_init_tools_is_idempotent():
    tools_init.init_tools()
    first_count = len(get_tool_registry().get_all_tools())

    tools_init.init_tools()
    second_count = len(get_tool_registry().get_all_tools())

    assert first_count == second_count


def test_init_tools_respects_optional_keys(monkeypatch):
    from src.config import settings

    monkeypatch.setattr(settings, "tavily_api_key", "", raising=False)
    monkeypatch.setattr(settings, "dashscope_api_key", "", raising=False)

    tools_init.init_tools()

    names = [t.name for t in get_tool_registry().get_tools("sub_agents")]
    assert names == ["learning_path_expert", "code_review_expert"]


def test_tools_from_registry_are_real_langchain_tools(monkeypatch):
    from src.config import settings

    monkeypatch.setattr(settings, "tavily_api_key", "tvly-fake", raising=False)
    monkeypatch.setattr(settings, "dashscope_api_key", "dash-fake", raising=False)
    tools_init.init_tools()
    tools = get_tool_registry().get_all_tools()

    assert tools
    for tool in tools:
        assert hasattr(tool, "name") and tool.name
        assert hasattr(tool, "description") and tool.description
        assert tool.args_schema is not None  # 结构化入参，模型才能正确调用

    # 抽查一个：注册中心里的工具与直接构造的工具同名同 schema
    by_name = {t.name: t for t in tools}
    reference = build_learning_path_tool()
    assert by_name["learning_path_expert"].args_schema is reference.args_schema
