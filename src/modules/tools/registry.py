# -*- coding: utf-8 -*-
"""
src/modules/tools/registry.py
作用：工具注册中心（Tool Registry）
    统一管理"所有可用工具"，包括：
      1. 手写 LangChain Tool（暂时保留，逐步替换成 MCP）
      2. MCP Server 提供的工具（未来的主力）
      3. 子 Agent 工具（把 learning_path / code_review 包成工具）

    按"分组"管理，每个 Agent / 总控可以按组拿自己需要的工具。

为什么需要它：
    - 工具统一接入、统一分配，不用每个 Agent 各自 import 工具
    - 加新工具只改这里，Agent 代码不动 → 复用性、扩展性最强
    - 总控和子 Agent 都从这里拿工具，权责清晰
"""

# ========== 导入部分 ==========

from typing import Dict, List, Optional
# Dict：工具分组字典
# List：工具列表
# Optional：可选类型

from langchain_core.tools import BaseTool
# BaseTool：LangChain 的工具基类
# 所有工具（手写的、MCP 转的、子 Agent 包的）最终都要是 BaseTool 子类
# 这样 create_agent(tools=...) 才能直接用


# ========== 工具注册中心类 ==========

class ToolRegistry:
    """
    工具注册中心
    用"分组"的方式管理工具，每个组对应一类用途（如 "search" 搜索组、"filesystem" 文件组）

    设计：
        - register_group(group_name, tools)：注册一组工具
        - get_tools(group_name)：拿某一组的工具
        - get_all_tools()：拿全部工具（给总控用）
        - register_mcp_tools(server_name, tools)：注册从 MCP server 发现的工具
    """

    def __init__(self):
        """构造函数：初始化分组字典"""
        self._groups: Dict[str, List[BaseTool]] = {}
        # _groups：私有字典，key 是组名（如 "search"），value 是该组的工具列表
        # 下划线开头表示"内部用"，外部不应该直接访问

        self._mcp_servers: Dict[str, List[BaseTool]] = {}
        # _mcp_servers：MCP server 名 → 它提供的工具列表
        # 单独存一份，方便后续按 server 管理（启停、刷新）

    # ===== 注册接口 =====

    def register_group(self, group_name: str, tools: List[BaseTool]) -> None:
        """
        注册一组工具

        参数:
            group_name: 组名（如 "search"、"filesystem"）
            tools:      工具列表（每个元素都是 BaseTool 子类）

        作用:
            把一批工具按"用途"归到一个组里，
            后面 Agent 可以按组拿自己需要的工具
        """
        if group_name in self._groups:
            # 如果组已存在，追加进去（不覆盖）
            self._groups[group_name].extend(tools)
        else:
            # 新组，直接赋值
            self._groups[group_name] = list(tools)
            # 用 list(tools) 复制一份，避免外部修改影响内部

    def register_tool(self, group_name: str, tool: BaseTool) -> None:
        """
        注册单个工具到指定组

        参数:
            group_name: 组名
            tool:       单个工具（BaseTool）
        """
        self.register_group(group_name, [tool])
        # 复用 register_group，传单元素列表

    def register_mcp_server(self, server_name: str, tools: List[BaseTool],
                            group_name: Optional[str] = None) -> None:
        """
        注册一个 MCP server 提供的所有工具

        参数:
            server_name: MCP server 名称（如 "tavily"）
            tools:       该 server 提供的工具列表（从 MCP client 自动发现得到）
            group_name:  可选，把这些工具归到哪个组；不填就用 server_name 当组名

        作用:
            MCP server 启动后，会返回一堆工具
            用这个方法把它们统一收进注册中心
        """
        # 存到 mcp_servers 字典
        self._mcp_servers[server_name] = tools

        # 决定归到哪个组
        group = group_name if group_name else server_name
        # 如果指定了 group_name 就用指定的，否则用 server_name 当组名

        # 注册到分组系统
        self.register_group(group, tools)
        # 复用 register_group，把 MCP 工具和普通手写工具一视同仁
        # 上层（Agent）根本不知道工具来自 MCP 还是手写，接口完全一致

    # ===== 查询接口 =====

    def get_tools(self, group_name: str) -> List[BaseTool]:
        """
        获取指定组的所有工具

        参数:
            group_name: 组名

        返回:
            该组的工具列表；组不存在返回空列表
        """
        return self._groups.get(group_name, [])
        # dict.get(key, default)：key 不存在时返回默认值
        # 这里默认是空列表，调用方不用处理 None

    def get_all_tools(self) -> List[BaseTool]:
        """
        获取所有分组的全部工具（合在一起）

        返回:
            全部工具的大列表
        给谁用:
            总控 Supervisor（它有权限调所有工具）
        """
        all_tools: List[BaseTool] = []
        for group_tools in self._groups.values():
            # 遍历每个分组
            all_tools.extend(group_tools)
            # 把每组的工具都追加到总列表
        return all_tools

    def list_groups(self) -> List[str]:
        """
        列出所有已注册的组名

        返回:
            组名列表
        """
        return list(self._groups.keys())

    def list_mcp_servers(self) -> List[str]:
        """
        列出所有已注册的 MCP server 名

        返回:
            server 名列表
        """
        return list(self._mcp_servers.keys())


# ========== 单例（全局唯一实例） ==========

# 为什么用单例：
#   工具注册中心整个应用只有一份就够了
#   各处都从 get_tool_registry() 拿同一个实例，保证工具注册是共享的

_tool_registry_instance: Optional[ToolRegistry] = None
# 私有全局变量：存唯一实例

def get_tool_registry() -> ToolRegistry:
    """
    获取工具注册中心的全局唯一实例（单例模式）

    返回:
        ToolRegistry 实例
    """
    global _tool_registry_instance
    # 声明使用全局变量

    if _tool_registry_instance is None:
        # 第一次调用时创建
        _tool_registry_instance = ToolRegistry()

    return _tool_registry_instance
    # 返回同一个实例
    # 以后每次调用都返回同一个对象，工具注册是全局共享的
