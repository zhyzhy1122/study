# -*- coding: utf-8 -*-
"""
src/modules/search/agent.py
作用：搜索 Agent（独立 Agent）
功能：
  - 网页搜索：输入关键词，返回搜索结果
  - 网页抓取：输入 URL，返回网页正文内容
  - Agent 自动判断什么时候搜、什么时候抓
技术：
  - create_agent 构建
  - 工具来源：MCP（Tavily 搜索 + Playwright 浏览器抓取），从工具注册中心获取
  - DeepSeek 模型

改造说明（P1）：
  旧版：内部手写两个 Tavily SDK 工具（tavily_search / tavily_extract），写死在 tools.py
  新版：连接 MCP server 自动发现的工具，从注册中心按组获取
        工具接入走注册中心 → 以后加新 MCP server 只改配置，本文件不用动
"""

# ========== 导入部分 ==========

import asyncio
# asyncio：标准库异步模块
# 用途：在同步 _run 里兜底执行异步初始化（其实主链路走 _arun，这是保底）

from langchain.agents import create_agent
# create_agent：LangChain v1 新标准 Agent 构造函数
# 签名：create_agent(model, tools, system_prompt)

from src.agents.base import BaseAgent
# Agent 基类，继承获得中间件 + 同步/异步双入口（run / arun）

from src.utils.llm import get_deepseek_llm
# DeepSeek 模型工厂

from src.modules.tools import get_tool_registry, init_mcp_tools
# 从工具注册中心导入：
#   get_tool_registry：拿注册中心单例
#   init_mcp_tools：异步初始化 MCP 工具（连接 server→发现工具→注册进中心）

# ========== 系统提示词 ==========

SEARCH_AGENT_SYSTEM_PROMPT = """你是一位专业的信息检索专家，擅长使用搜索工具快速找到准确、有用的信息。

你的工作流程：
1. 分析用户的信息需求
2. 选择合适的搜索关键词进行搜索
3. 查看搜索结果的摘要，判断是否需要进一步抓取详情
4. 如果需要具体内容，使用抓取工具读取相关网页的正文
5. 整理搜索到的信息，给出清晰、准确的回答

工作原则：
- 搜索关键词要精准，不要太宽泛
- 优先看搜索结果的摘要，再决定要不要抓详情
- 抓取网页时，选择最相关、最权威的来源
- 回答要基于搜索结果，不要编造信息
- 如果搜索不到相关信息，直接告诉用户
- 注明信息来源（标题和链接）
- 支持多个平台/来源的搜索结果，包括但不限于：官方文档、技术社区、B站/视频、知乎、
  GitHub、掘金等，根据用户需求偏好不同来源
- 直接开始检索并给结果，不要菜单式开场白、不要自我介绍

输出格式：
- 先给出答案/结论
- 然后列出参考来源（标题 + 链接）
"""

# SEARCH_AGENT_SYSTEM_PROMPT：搜索 Agent 的系统提示词
# 告诉它的角色、工作流程、原则、输出格式
# 多平台来源是在工作原则里提示，不写死具体网站名 → 支持未来扩展

# 定义了"搜索 Agent 应该拿到哪些工具组"
SEARCH_AGENT_TOOL_GROUPS = ["mcp_tavily", "mcp_playwright"]
# 这两个组名的含义（来自 mcp_client.py 的 register_mcp_tools）：
#   每个 server 的工具会同时注册到：
#     group_name（默认 "mcp_search"）—— 通用组
#     f"mcp_{server_name}" —— 按 server 名的组（mcp_tavily / mcp_playwright）
# 这里明确按 server 名取，语义清晰：搜索用 tavily，抓详情用 playwright

# ========== 搜索 Agent 类 ==========

class SearchAgent(BaseAgent):
    """
    搜索 Agent
    继承自 BaseAgent，拥有中间件 + 反射评估 + 打回重跑支持
    内部用 create_agent 构建，工具从注册中心按组获取（MCP 工具）
    """

    def __init__(self):
        """构造函数"""
        super().__init__(name="search")
        # name 设为 "search"，与注册表 key 一致（get_agent("search") 用到）

        self._agent = None
        # 内部的 create_agent 实例，懒加载（首次异步初始化后缓存）

    def _get_tools(self):
        """
        从注册中心取当前可用的 MCP 工具（同步，仅读缓存）
        前提：init_mcp_tools() 已执行过（否则对应组为空）

        返回:
            list：注册中心里该组下的全部工具（BaseTool 列表）
        """
        registry = get_tool_registry()
        # 拿注册中心单例（全应用共享同一份）

        tools = []
        for group in SEARCH_AGENT_TOOL_GROUPS:
            tools.extend(registry.get_tools(group))
            # get_tools(group)：取某组的全部工具（组不存在返回空列表）
        return tools
        # 把 tavily + playwright 两组工具合并返回

    async def _aget_agent(self, _first_try=True):
        """
        【异步】获取内部的 create_agent 实例（懒加载 + 自动初始化 MCP）

        返回:
            create_agent 实例

        做法：
        - 首次调用：先 await init_mcp_tools()（连接 server → 工具注册进中心），再取工具建 agent
        - 之后调用：直接从缓存 _agent 返回，不再重复初始化
        """

        if self._agent is not None:
            # 已经建好，直接复用
            return self._agent

        # 首次：确保 MCP 工具已初始化（幂等，重复调用安全）
        await init_mcp_tools()
        # 内部：连接 tavily + playwright → 发现工具 → 注册到 mcp_search/mcp_tavily/mcp_playwright
        # 幂等：已初始化过会直接返回 0，不重复连接

        tools = self._get_tools()
        # 从注册中心取 MCP 工具（tavily + playwright）

        if not tools:
            # 极端情况：工具为空（可能 MCP server 启动失败 / 没配 key）
            raise RuntimeError(
                "搜索 Agent 未获取到 MCP 工具，请检查 Tavily/Playwright MCP server 是否初始化成功"
            )
            # 明确抛错，避免 create_agent(tools=[]) 建出个"没工具的空壳 Agent"

        llm = get_deepseek_llm(temperature=0.3)
        # 搜索要准确，温度设低一点

        self._agent = create_agent(
            model=llm,
            # 模型：DeepSeek

            tools=tools,
            # 工具列表：从注册中心拿的 MCP 工具（tavily 搜索 + playwright 抓取）

            system_prompt=SEARCH_AGENT_SYSTEM_PROMPT,
            # 系统提示词
        )

        return self._agent

    # ===== 异步版本（主链路） =====

    async def _arun(self, user_input: str, before_ctx: dict = None, **kwargs) -> str:
        """
        【异步】执行搜索任务

        参数:
            user_input: 用户的搜索需求
            before_ctx: 中间件上下文
            **kwargs:   额外参数

        返回:
            搜索结果（整理后的回答）
        """
        agent = await self._aget_agent()
        # 异步懒加载 agent（首次会用 init_mcp_tools 初始化 MCP 工具）

        messages = [
            {"role": "user", "content": user_input}
        ]

        result = await agent.ainvoke({"messages": messages})
        # 异步调用 agent

        final_answer = result["messages"][-1].content
        return final_answer

    # ===== 同步版本（兜底，仅读缓存） =====

    def _run(self, user_input: str, before_ctx: dict = None, **kwargs) -> str:
        """
        【同步】执行搜索任务
        注意：MCP 工具初始化是异步的，同步入口不负责初始化
        第一个跑的人必须先通过异步路径初始化过一次（或已缓存 agent）

        参数:
            user_input: 用户的搜索需求
            before_ctx: 中间件上下文
            **kwargs:   额外参数

        返回:
            搜索结果（整理后的回答）
        """
        if self._agent is None:
            # 缓存里还没有 agent（还没做过异步初始化）
            # 抛明确错误，引导走异步路径
            raise RuntimeError(
                "SearchAgent 同步调用时 MCP 工具尚未初始化，"
                "请先用异步入口 arun() 初始化一次"
            )

        agent = self._agent
        # 直接用已缓存的 agent

        messages = [
            {"role": "user", "content": user_input}
        ]

        result = agent.invoke({"messages": messages})
        # 同步调用 agent.invoke

        final_answer = result["messages"][-1].content
        return final_answer

# ========== 文件结束 ==========
