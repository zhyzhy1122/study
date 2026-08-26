# -*- coding: utf-8 -*-
"""
src/modules/clarify/agent.py
作用：需求澄清 Agent —— 需求太宽泛时，中断并向用户抛出选项卡片

机制：
  总控(create_agent) 发现需求宽泛 → 调用 clarify_expert 工具
    → 工具内部 interrupt(ClarifyRequest) → 图暂停
    → 外部展示卡片给用户 → 用户选择后 Command(resume=choice) 恢复
    → interrupt 原地返回 choice → 工具把 choice 返回给总控

为什么放 modules/：
  和 learning_path / code_review / search 平级，是"一个独立业务 Agent"

为什么 interrupt 在工具里而不是中间件：
  interrupt 是 LangGraph 图层机制，必须在带 checkpointer 的图执行流内
  工具是被 create_agent 图调用执行的，天然在图里 → 这是最正统、踩坑最少的放法
"""

# ========== 导入部分 ==========

from langgraph.types import interrupt
# interrupt：LangGraph 的暂停原语
# 调用 interrupt(payload) 会让"当前图执行"暂停，把 payload 抛给外部
# 之后用 Command(resume=...) 恢复时，interrupt() 原地返回 resume 传入的值

from langchain_core.tools import StructuredTool
# StructuredTool：把普通 python 函数包装成 LangChain 工具（带参数 schema）
# 让 create_agent 能发现并调用它

from pydantic import BaseModel, Field
# BaseModel / Field：定义"工具入参"的 pydantic 模型

from src.schema.clarify import ClarifyRequest, ClarifyOption
# 复用你已有的澄清数据结构：
#   ClarifyRequest：一次澄清请求（question + options + allow_more）
#   ClarifyOption：单个选项（id / title / desc / points）
# 这样中断抛出去的就是结构化卡片，前端能直接渲染

# ========== 工具入参模型 ==========

class ClarifyInput(BaseModel):
    """
    澄清工具的入参（总控/model 传给工具的）
    为什么不直接传 ClarifyRequest：
      - ClarifyRequest 里 options 是"列表"，模型很难一次生成结构规范的列表
      - 拆成 A/B/C 三个明确字段，模型生成更稳定、更不容易出错
    """
    question: str = Field(..., description="向用户提出的问题")
    # question：卡片顶部的引导语，如"你想学 AI，请选择一条路线："

    option_a: str = Field(..., description="选项 A 的标题")
    # 选项A标题

    option_a_desc: str = Field("", description="选项 A 的一句话描述")
    # 选项A描述（可空）

    option_b: str = Field(..., description="选项 B 的标题")
    option_b_desc: str = Field("", description="选项 B 的一句话描述")

    option_c: str = Field(..., description="选项 C 的标题")
    option_c_desc: str = Field("", description="选项 C 的一句话描述")

# ========== interrupt 触发核心 ==========

def _request_clarification(request: ClarifyRequest) -> str:
    """
    发起澄清：调用 interrupt 暂停，等待用户选择

    参数:
        request: 构造好的 ClarifyRequest（含 question + options）

    返回:
        str：用户的选择（如 'A'、'B'、'C' 或自由文本）

    原理（关键，请理解这行）：
      - interrupt(request) 让图在此刻暂停
      - request（ClarifyRequest）作为"载荷"被抛给外部（前端/命令行）
      - 外部展示后，用户选择，调用方用 Command(resume=选中的值) 再触发图
      - 此时 interrupt() 原地返回 resume 传入的那个值 → 就是用户的 choice
      这就是"暂停 → 问 → 恢复 → 拿到答案"的本质
    """
    choice = interrupt(request)
    # 图在这里暂停！返回的 choice = 外部 resume(choice) 时传来的值

    return choice if isinstance(choice, str) else str(choice)
    # 规范化为字符串返回，保证总控拿到的是 str

# ========== 构造澄清工具 ==========

def _clarify_func(
    question: str,
    option_a: str,
    option_a_desc: str,
    option_b: str,
    option_b_desc: str,
    option_c: str,
    option_c_desc: str,
) -> str:
    """
    工具的底层执行函数：把入参拼成 ClarifyRequest，再触发 interrupt

    参数:
        question / option_a...option_c_desc：即 ClarifyInput 的 7 个字段，
        由 StructuredTool 从模型传入的 JSON 按 args_schema 解包后填入

    返回:
        str：用户的选择
    """
    request = ClarifyRequest(
        question=question,
        options=[
            # 把 A/B/C 三个入参转成 ClarifyOption 列表
            # id 固定 'A'/'B'/'C'，前端点选后回传
            ClarifyOption(id="A", title=option_a, desc=option_a_desc),
            ClarifyOption(id="B", title=option_b, desc=option_b_desc),
            ClarifyOption(id="C", title=option_c, desc=option_c_desc),
        ],
    )
    # 构造 ClarifyRequest（复用 schema/clarify.py）

    return _request_clarification(request)
    # 触发 interrupt 暂停，并返回用户选择

def build_clarify_tool() -> StructuredTool:
    """
    构建澄清工具（供总控 create_agent 使用）

    返回:
        StructuredTool：名为 clarify_expert 的工具

    作用：
      总控的可调用工具之一，挂进工具列表后，总控判断需求宽泛时就能调它
      从而触发 interrupt 人机交互
    """
    return StructuredTool.from_function(
        func=_clarify_func,
        # 底层执行函数
        name="clarify_expert",
        # 工具名，总控提示词/日志里用它
        description=(
            "当用户的需求过于宽泛、信息不足时调用此工具，"
            "向用户澄清并选择方向。传入一个问题（question）和 A/B/C 三个选项。"
        ),
        # 工具描述：告诉总控"什么时候该用"
        args_schema=ClarifyInput,
        # 入参 schema：总控/模型按这个生成 JSON 参数
    )