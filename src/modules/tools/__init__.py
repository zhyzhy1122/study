# -*- coding: utf-8 -*-
"""
src/modules/tools/__init__.py
作用：工具注册中心包入口
统一导出 ToolRegistry 和子 Agent 工具构建函数
上层（总控/子 Agent）都从这里拿工具
"""

from src.modules.tools.registry import ToolRegistry, get_tool_registry
from src.modules.tools.agent_tools import (
    build_all_agent_tools,
    build_learning_path_tool,
    build_code_review_tool,
    build_search_tool,
)
from src.modules.tools.init_tools import init_tools, init_mcp_tools
from src.modules.tools.mcp_client import (
    build_mcp_connections,
    load_mcp_tools,
    register_mcp_tools,
)

__all__ = [
    "ToolRegistry",
    "get_tool_registry",
    "build_all_agent_tools",
    "build_learning_path_tool",
    "build_code_review_tool",
    "build_search_tool",
    "init_tools",
    "init_mcp_tools",
    "build_mcp_connections",
    "load_mcp_tools",
    "register_mcp_tools",
]
