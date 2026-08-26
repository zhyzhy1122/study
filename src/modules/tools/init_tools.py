# -*- coding: utf-8 -*-
"""
src/modules/tools/init_tools.py
作用：工具注册中心的初始化入口
    同步 init_tools()：注册不需要异步加载的工具（子 Agent 工具、手写工具）
    异步 init_mcp_tools()：注册 MCP 工具（需要连 server，所以是异步的）

为什么分同步/异步两个初始化：
    - 子 Agent 工具是纯内存构造，同步就能完成
    - MCP 工具需要连接 server（起子进程 / 连网络），必须异步
    - 上层按需调用：只需要子 Agent 工具就调 init_tools()
                  需要 MCP 工具就再调 await init_mcp_tools()
"""

# ========== 导入部分 ==========

from src.config import settings
# 全局配置（含 Tavily API Key 等）

from src.modules.tools.registry import get_tool_registry
# 导入注册中心单例

from src.modules.tools.agent_tools import build_all_agent_tools
# 导入子 Agent 工具构建函数

from src.modules.tools.mcp_client import register_mcp_tools
# 导入 MCP 工具注册函数（异步）


# ========== 同步初始化（子 Agent 工具等立即可用的） ==========

_initialized = False
# 标记是否已经初始化过，防止重复注册

def init_tools() -> None:
    """
    【同步】初始化工具注册中心（幂等：重复调用不会重复注册）

    做什么:
        1. 注册子 Agent 工具组（"sub_agents" 组）
        2. 其它手写工具组（未来扩展）

    调用时机:
        - Supervisor 创建前
        - FastAPI 应用启动时（lifespan 同步部分）

    注意:
        这个函数只注册"立即可用"的工具，不涉及 MCP
        MCP 工具需要单独调 init_mcp_tools()（异步）
    """
    global _initialized
    if _initialized:
        # 已经初始化过了，直接返回
        return

    registry = get_tool_registry()
    # 拿到注册中心单例

    # --- 1. 注册子 Agent 工具组 ---
    agent_tools = build_all_agent_tools()
    # 构建全部 3 个子 Agent 工具（learning_path / code_review / search）
    registry.register_group("sub_agents", agent_tools)
    # 注册到 "sub_agents" 组
    # 总控从注册中心拿 "sub_agents" 组就能拿到所有子 Agent 工具

    # --- 2. 其它手写工具组（未来扩展） ---
    # ...

    _initialized = True
    # 标记已初始化，防止重复注册


# ========== 异步初始化（MCP 工具） ==========

_mcp_initialized = False
# MCP 初始化标记（单独一个，因为是异步的）

async def init_mcp_tools() -> int:
    """
    【异步】初始化 MCP 工具（幂等：重复调用不会重复注册）

    做什么:
        1. 确保同步初始化已完成（init_tools）
        2. 连接所有配置的 MCP Server
        3. 自动发现工具
        4. 注册到 ToolRegistry 的 "mcp" 相关组

    返回:
        int：注册的 MCP 工具总数

    调用时机:
        - 需要用到 MCP 工具之前
        - FastAPI lifespan 的异步启动部分

    说明:
        之所以和 init_tools 分开，是因为 MCP 需要启动子进程、连网络，
        是较重的操作，而且必须异步。让调用方自己决定什么时候连。
    """
    global _mcp_initialized
    if _mcp_initialized:
        # 已经初始化过了，返回 0 表示没新增
        return 0

    # 先确保同步初始化完成
    init_tools()

    registry = get_tool_registry()
    # 拿到注册中心

    # 调用 MCP 工具注册函数（异步）
    count = await register_mcp_tools(
        registry=registry,
        tavily_api_key=settings.tavily_api_key,
        # 从 settings 拿 Tavily API Key
        group_name="mcp_search",
        # MCP 搜索类工具统一注册到 "mcp_search" 组
    )

    _mcp_initialized = True
    # 标记 MCP 已初始化

    return count
    # 返回注册的工具总数，方便日志打印
