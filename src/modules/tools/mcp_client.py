# -*- coding: utf-8 -*-
"""
src/modules/tools/mcp_client.py
作用：MCP 客户端管理器
    封装 MultiServerMCPClient，统一管理所有 MCP Server 的连接、工具发现、注册到 ToolRegistry

为什么单独一层：
    - MCP 连接的配置、启动、工具发现是独立关注点
    - 以后加新的 MCP Server，只要改配置（settings / .env），不用改业务代码
    - 统一处理连接生命周期（启动、关闭、错误重试）

支持的 transport：
    - stdio：本地子进程（最常用，Tavily / Playwright / Filesystem 都是这种）
    - http / sse：远程 HTTP 服务（企业内部服务等）
"""

# ========== 导入部分 ==========

from typing import Dict, List, Optional
# Dict：MCP server 配置字典
# List：工具列表
# Optional：可选类型

from langchain_core.tools import BaseTool
# BaseTool：LangChain 工具基类
# MCP 工具最终也会被转成 BaseTool，和手写工具一视同仁

from langchain_mcp_adapters.client import MultiServerMCPClient
# MultiServerMCPClient：langchain-mcp-adapters 提供的多 server MCP 客户端
# 支持同时连多个 MCP server，统一管理
# 输入：connections 配置字典
# 输出：get_tools() 返回 LangChain BaseTool 列表


# ========== MCP 配置管理 ==========

def build_mcp_connections(tavily_api_key: str) -> Dict[str, dict]:
    """
    构建 MCP Server 的连接配置字典

    参数:
        tavily_api_key: Tavily 的 API Key（从 settings / .env 来）

    返回:
        Dict[str, dict]：MultiServerMCPClient 接受的 connections 格式
        key = server 名称（自定义，用来标识）
        value = 连接配置（含 transport、command、args、env 等）

    当前配置的 MCP Server：
        1. tavily：联网搜索（npx 启动 @modelcontextprotocol/server-tavily）
        2. playwright：浏览器自动化（npx 启动 @playwright/mcp）
        以后要加新 server，在这个函数里加一项就行
    """
    connections: Dict[str, dict] = {}
    # 用字典收集要启动的 MCP server
    # 没配置 Key 的服务直接跳过，不会启动失败

    # --- 1. Tavily 搜索 MCP Server（可选，有 Key 才启动）---
    if tavily_api_key:
        # 只有 Tavily API Key 非空时才加入
        # 没配置 Key → 跳过 → 搜索功能不可用，但程序不会崩
        connections["tavily"] = {
            "command": "npx",
            "args": ["-y", "tavily-mcp"],
            "transport": "stdio",
            "env": {
                "TAVILY_API_KEY": tavily_api_key,
            },
        }

    # --- 2. Playwright 浏览器自动化 MCP Server ---
    # Playwright 官方 MCP server，让 Agent 能操作浏览器
    connections["playwright"] = {
        "command": "npx",
        "args": ["-y", "@playwright/mcp@latest"],
        # @playwright/mcp 是 Playwright 官方的 MCP server
        # @latest 确保用最新版
        "transport": "stdio",
        # 同样 stdio 模式
        # Playwright 不需要 API key，本地操作浏览器
    }

    # --- 以后加新的 MCP Server，在这里复制一段即可 ---
    # connections["github"] = {
    #     "command": "npx",
    #     "args": ["-y", "@modelcontextprotocol/server-github"],
    #     "transport": "stdio",
    #     "env": {"GITHUB_TOKEN": github_token},
    # }

    return connections
    # 返回完整的 connections 配置字典


# ========== MCP 工具加载器 ==========

async def load_mcp_tools(connections: Dict[str, dict]) -> Dict[str, List[BaseTool]]:
    """
    连接所有 MCP Server，自动发现工具，按 server 分组返回

    参数:
        connections: MCP server 连接配置字典（build_mcp_connections 的返回值）

    返回:
        Dict[str, List[BaseTool]]：key = server 名，value = 该 server 提供的工具列表

    说明:
        - 直接实例化 MultiServerMCPClient（不能用 async with，0.3.x 已废弃）
        - get_tools() 会自动连接每个 server、列出工具、转成 LangChain BaseTool
        - 这里我们按 server 分组返回，方便后面注册到 ToolRegistry 的不同组
        - 注意：MultiServerMCPClient 的工具调用是"每次调用新建 session"，
          所以不需要显式关闭连接，工具对象本身就携带了连接信息
    """
    client = MultiServerMCPClient(
        connections,
        # 传入全部 server 的连接配置
        tool_name_prefix=True,
        # 工具名加 server 名前缀，避免重名
        # 比如 tavily 的 tavily_search 变成 tavily_tavily_search
        # 多个 server 都有 search 工具时不会冲突
    )
    # 直接实例化，不用 async with
    # 0.3.x 版本不支持作为 context manager

    result: Dict[str, List[BaseTool]] = {}
    # 存放结果：server 名 → 工具列表

    for server_name in connections.keys():
        # 遍历每个配置的 server
        tools = await client.get_tools(server_name=server_name)
        # 从指定 server 获取工具
        # 内部会自动：连接 server → 列出工具 → 转成 BaseTool
        result[server_name] = tools
        # 按 server 分组存起来

    return result
    # 返回分组后的工具字典
    # 工具对象内部携带了 server 连接配置，每次调用时自动建立 session


# ========== 注册到 ToolRegistry ==========

async def register_mcp_tools(
    registry,
    tavily_api_key: str,
    group_name: str = "mcp_search",
) -> int:
    """
    连接 MCP Server，发现工具，并注册到 ToolRegistry

    参数:
        registry:       ToolRegistry 实例（工具注册中心）
        tavily_api_key: Tavily API Key
        group_name:     MCP 工具注册到哪个组（默认 "mcp_search"）
                        以后加不同类别的 MCP 工具，可以分不同组

    返回:
        int：注册的工具总数（方便日志确认）

    用法:
        registry = get_tool_registry()
        count = await register_mcp_tools(registry, "tvly-xxx")
        print(f"注册了 {count} 个 MCP 工具")
    """
    # 1. 构建连接配置
    connections = build_mcp_connections(tavily_api_key)

    # 2. 连接 MCP server，加载工具
    tools_by_server = await load_mcp_tools(connections)
    # tools_by_server 是 {server_name: [tool1, tool2, ...]}

    # 3. 注册到 ToolRegistry
    total = 0
    for server_name, tools in tools_by_server.items():
        # 每个 server 的工具单独注册
        # 同时注册到"通用 mcp 组"和"按 server 名的组"
        registry.register_group(group_name, tools)
        # 通用组：mcp_search（或调用方指定的名字）
        registry.register_group(f"mcp_{server_name}", tools)
        # 按 server 名的组：mcp_tavily / mcp_playwright
        # 这样以后可以按组拿工具，也可以按 server 名精确拿
        total += len(tools)
        # 累加工具计数

    return total
    # 返回总注册数
