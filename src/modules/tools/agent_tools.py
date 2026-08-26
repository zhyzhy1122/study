# -*- coding: utf-8 -*-
"""
src/modules/tools/agent_tools.py
作用：把子 Agent 包装成"总控可以像调工具一样调用"的 LangChain Tool
    每个子 Agent（learning_path / code_review / search）对外暴露成一个 StructuredTool
    总控拿到这个 tool，调用它就等于"派给这位专家处理"

内部 vs 外部：
    - 外部（总控视角）：就是一个普通工具，有名字、有描述、有输入输出
    - 内部（实现）：真正调的是完整的子 Agent（带中间件、带反思评估）
    这就是"接口工具化、实现独立化"——子 Agent 仍是独立 Agent，只是对总控的接口是工具形态

与工具注册中心的关系：
    本文件提供 build_agent_tools()，产出的工具列表注册到 ToolRegistry
    总控从注册中心拿"子 Agent 工具组"即可
"""

# ========== 导入部分 ==========

from typing import List
# List：工具列表类型

from langchain_core.tools import StructuredTool, BaseTool
# StructuredTool：LangChain 的结构化工具基类
# 用它可以把一个异步函数包装成支持 ainvoke 的 Tool
# 比 @tool 装饰器更灵活，支持异步 + 自定义 name/description

from pydantic import BaseModel, Field
# BaseModel：工具输入参数的 schema 基类
# Field：给字段加描述（模型会读这个描述来判断什么时候调这个工具）

from src.agents.base import get_agent
# 从 base.py 导入 get_agent 工厂函数
# 用它拿到子 Agent 实例（learning_path / code_review / search）

from src.modules.multimodal import MultimodalAgent

# ========== 每个子 Agent 的输入 schema ==========
# 为什么要单独定义 schema：
#   工具调用时，模型要知道"这个工具需要什么参数、每个参数是什么意思"
#   用 pydantic BaseModel 定义，模型能精确理解每个字段的含义
#   比"只有一个 query 字符串"更清晰、调用更准确

class LearningPathInput(BaseModel):
    """学习路线规划 Agent 的输入参数"""
    user_query: str = Field(..., description="用户关于学习路线的完整需求，越详细越好。例如：'我是零基础，想用3个月入门AI应用开发，请给出详细学习路线'")


class CodeReviewInput(BaseModel):
    """代码审查 Agent 的输入参数"""
    code_content: str = Field(..., description="需要被审查的代码内容（完整代码文本）")
    language: str = Field("python", description="代码的编程语言，如 python / javascript / java 等")
    extra_requirements: str = Field("", description="额外的审查要求，例如'重点看性能问题'、'检查安全漏洞'等")


class SearchInput(BaseModel):
    """联网搜索 Agent 的输入参数"""
    query: str = Field(..., description="搜索关键词或问题。例如：'PyTorch 2.0 新特性有哪些'")
    max_results: int = Field(5, description="最多返回多少条结果，默认5条")
    need_extract: bool = Field(False, description="是否需要深入抓取网页正文内容，True=需要，False=只看摘要")


# ========== 每个子 Agent 的执行函数 ==========
# 同步 + 异步双版本
# 同步版本：_run_xxx
# 异步版本：_arun_xxx（带 async + await，总控异步调用用这个）

def _run_learning_path(user_query: str) -> str:
    """
    【同步】调用学习路线规划 Agent
    内部拿到 learning_path Agent，执行它的 run()

    参数:
        user_query: 用户的学习路线需求

    返回:
        Agent 生成的完整学习路线（字符串）
    """
    agent = get_agent("learning_path")
    # 从工厂拿到 learning_path Agent 实例（懒加载，第一次调用时创建）
    return agent.run(user_query)
    # 同步执行：走 BaseAgent.run() → 含中间件 + 反思评估 + 打回重跑

async def _arun_learning_path(user_query: str) -> str:
    """
    【异步】调用学习路线规划 Agent
    与上面同步版逻辑完全一致，只是用 await 调 arun()

    参数:
        user_query: 用户的学习路线需求

    返回:
        Agent 生成的完整学习路线（字符串）
    """
    agent = get_agent("learning_path")
    # 拿到实例（同步/异步共用同一个实例）
    return await agent.arun(user_query)
    # await 异步执行：走 BaseAgent.arun() → 含中间件 + 反思评估 + 打回重跑


def _run_code_review(code_content: str, language: str = "python",
                     extra_requirements: str = "") -> str:
    """
    【同步】调用代码审查 Agent

    参数:
        code_content:        待审查代码
        language:            编程语言
        extra_requirements:  额外要求

    返回:
        审查报告（字符串）
    """
    agent = get_agent("code_review")
    # 拿到 code_review Agent
    user_input = (
        f"请审查以下 {language} 代码：\n\n"
        f"```\n{code_content}\n```\n\n"
        f"额外要求：{extra_requirements if extra_requirements else '无'}"
    )
    # 把结构化参数拼成 Agent 能理解的自然语言输入
    return agent.run(user_input)

async def _arun_code_review(code_content: str, language: str = "python",
                            extra_requirements: str = "") -> str:
    """
    【异步】调用代码审查 Agent
    """
    agent = get_agent("code_review")
    user_input = (
        f"请审查以下 {language} 代码：\n\n"
        f"```\n{code_content}\n```\n\n"
        f"额外要求：{extra_requirements if extra_requirements else '无'}"
    )
    return await agent.arun(user_input)


def _run_search(query: str, max_results: int = 5, need_extract: bool = False) -> str:
    """
    【同步】调用联网搜索 Agent

    参数:
        query:        搜索关键词
        max_results:  最多返回条数
        need_extract: 是否抓取正文

    返回:
        搜索结果（字符串）
    """
    agent = get_agent("search")
    # 拿到 search Agent
    user_input = (
        f"请帮我搜索：{query}\n"
        f"要求：最多 {max_results} 条结果\n"
        f"是否深入抓取网页正文：{'是' if need_extract else '否'}"
    )
    return agent.run(user_input)

async def _arun_search(query: str, max_results: int = 5, need_extract: bool = False) -> str:
    """
    【异步】调用联网搜索 Agent
    """
    agent = get_agent("search")
    user_input = (
        f"请帮我搜索：{query}\n"
        f"要求：最多 {max_results} 条结果\n"
        f"是否深入抓取网页正文：{'是' if need_extract else '否'}"
    )
    return await agent.arun(user_input)


# ========== 把函数包装成 LangChain Tool ==========
# 用 StructuredTool 构造，因为它：
#   1. 支持 name / description 自定义（模型看 description 决定什么时候调）
#   2. 支持 args_schema（输入参数结构化，比 @tool 装饰器更明确）
#   3. 支持 func + coroutine（同步 + 异步都能挂）

def build_learning_path_tool() -> StructuredTool:
    """
    构建"学习路线规划专家"工具
    把 learning_path Agent 包装成一个 LangChain Tool

    返回:
        StructuredTool：总控可以直接拿去 create_agent(tools=[...])
    """
    return StructuredTool(
        name="learning_path_expert",
        # 工具名：总控 Agent 调用时用这个名字
        # 命名要清晰，模型一看到就知道"这是学习路线专家"

        description=(
            "学习路线规划专家。"
            "当用户询问学习路径、技能提升计划、入门路线、学习资源推荐、"
            "AI/编程/算法等领域的学习规划时，调用此工具。"
            "输入：用户完整的学习需求。"
            "输出：详细的分阶段学习路线、知识点、难点、资源推荐。"
        ),
        # description 非常重要——模型靠读这段描述来判断"什么时候该调这个工具"
        # 写得越清楚，模型调用越准确
        # 要包含：什么场景用、输入是什么、输出是什么

        func=_run_learning_path,
        # 同步版本的实现函数
        # 总控同步调用时走这个

        coroutine=_arun_learning_path,
        # 异步版本的实现函数
        # 总控异步调用（ainvoke / astream）时走这个

        args_schema=LearningPathInput,
        # 输入参数的 schema（pydantic 模型）
        # 模型会读这个 schema 来构造调用参数
        # 比只有一个字符串参数更结构化、更准确
    )


def build_code_review_tool() -> StructuredTool:
    """
    构建"代码审查专家"工具
    """
    return StructuredTool(
        name="code_review_expert",
        description=(
            "代码审查专家。"
            "当用户提交代码、要求审查代码质量、找 bug、优化性能、检查安全问题、"
            "代码风格建议、最佳实践审查时，调用此工具。"
            "输入：完整的代码内容 + 编程语言 + 可选的额外审查要求。"
            "输出：详细的代码审查报告。"
        ),
        func=_run_code_review,
        coroutine=_arun_code_review,
        args_schema=CodeReviewInput,
    )


def build_search_tool() -> StructuredTool:
    """
    构建"联网搜索专家"工具
    注意：这是 search Agent 整体包装成的工具（专家级搜索）
         不是单个搜索函数（未来 MCP 搜索工具会是另一个更细粒度的工具）
    """
    return StructuredTool(
        name="search_expert",
        description=(
            "联网搜索专家。"
            "当用户需要查找最新信息、实时数据、新闻、技术文档、教程、"
            "或任何需要联网才能获取的信息时，调用此工具。"
            "输入：搜索关键词/问题 + 可选的结果数量 + 是否深入抓取正文。"
            "输出：整理好的搜索结果摘要 + 来源链接。"
        ),
        func=_run_search,
        coroutine=_arun_search,
        args_schema=SearchInput,
    )

def build_multimodal_tool() -> BaseTool:
    """
    构建多模态 Agent 工具（图片理解专家）

    返回:
        BaseTool：名为 multimodal_expert 的工具

    工具参数:
        user_input: 文字问题/提示
        image_path: 本地图片路径（可选，三选一）
        image_url: 图片 URL（可选，三选一）
        image_base64: base64 图片（可选，三选一）

    说明:
      总控发现用户上传了图片、或需要理解视觉内容时，调用此工具。
      工具内部调 MultimodalAgent.arun() → Qwen-VL → 返回文字回答。
      多模态 Agent 异步跑，不阻塞。
    """
    from pydantic import BaseModel, Field
    # Pydantic 定义工具入参 schema
    # 局部导入，避免顶层依赖

    class MultimodalInput(BaseModel):
        """多模态工具入参：文字问题 + 图片（三选一）"""
        user_input: str = Field(..., description="用户的问题或提示，描述你想从图片里了解什么")
        image_path: str = Field("", description="本地图片文件路径（和 image_url / image_base64 三选一）")
        image_url: str = Field("", description="公网图片 URL（和 image_path / image_base64 三选一）")
        image_base64: str = Field("", description="base64 编码的图片（和 image_path / image_url 三选一）")

    async def _arun_multimodal(
        user_input: str,
        image_path: str = "",
        image_url: str = "",
        image_base64: str = "",
    ) -> str:
        """异步调用多模态 Agent"""
        agent = get_agent("multimodal")
        # 从注册表拿实例（懒加载，首次创建）

        # 组装 kwargs（把图片参数透传给 MultimodalAgent._arun）
        kwargs = {}
        if image_path:
            kwargs["image_path"] = image_path
        if image_url:
            kwargs["image_url"] = image_url
        if image_base64:
            kwargs["image_base64"] = image_base64

        return await agent.arun(user_input, **kwargs)
        # 走异步主链路（中间件 + _arun + 反思评估）

    def _run_multimodal(
        user_input: str,
        image_path: str = "",
        image_url: str = "",
        image_base64: str = "",
    ) -> str:
        """同步调用多模态 Agent（兜底）"""
        agent = get_agent("multimodal")
        kwargs = {}
        if image_path:
            kwargs["image_path"] = image_path
        if image_url:
            kwargs["image_url"] = image_url
        if image_base64:
            kwargs["image_base64"] = image_base64
        return agent.run(user_input, **kwargs)

    return StructuredTool.from_function(
        func=_run_multimodal,
        # 同步版本（StructuredTool 要求提供同步函数）
        coroutine=_arun_multimodal,
        # 异步版本（异步调用时用这个）
        name="multimodal_expert",
        description=(
            "当用户提供了图片/截图，或需要理解视觉内容（报错截图、图表、界面、照片）时调用。"
            "传入问题(user_input)和图片（image_path 或 image_url 或 image_base64，三选一）。"
            "返回图片理解的文字结果。"
        ),
        args_schema=MultimodalInput,
    )
# ========== 批量构建函数 ==========

def build_all_agent_tools() -> List[StructuredTool]:
    """
    构建所有子 Agent 工具（一次性全部拿出来）

    返回:
        3 个子 Agent 工具的列表：
        [learning_path_tool, code_review_tool, search_tool]

    给谁用:
        注册中心（ToolRegistry）—— 调这个函数拿全部子 Agent 工具，再注册到 "sub_agents" 组
    """
    return [
        build_learning_path_tool(),
        build_code_review_tool(),
        build_search_tool(),
        build_multimodal_tool()
        # 澄清工具暂不挂载：当前 API 没有 thread_id/恢复机制，
        # interrupt 在无 checkpointer 的链路上会失败并导致空回答。
        # 等 Human-in-the-loop 恢复流程接通后再加回 build_clarify_tool()。
    ]
