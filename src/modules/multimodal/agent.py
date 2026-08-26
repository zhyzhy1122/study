# -*- coding: utf-8 -*-
"""
src/modules/multimodal/agent.py
作用：多模态 Agent（图片理解专家）

功能：
  - 接收图片 + 文字问题，返回图片理解结果
  - 支持报错截图识别、图片描述、图表分析等
  - 底层调用 Qwen-VL 多模态模型

继承 BaseAgent，获得：
  - 中间件（日志 / 记忆 / 反思评估）
  - 同步/异步双入口
  - Agent 注册表统一管理
"""

# ========== 导入部分 ==========

from src.agents.base import BaseAgent
# 继承基类，获得中间件 + 双入口能力

from src.modules.multimodal.qwen_vl import call_qwen_vl
# 底层 Qwen-VL 调用封装
# 模型层和 Agent 层分离，以后换模型只改 qwen_vl.py

# ========== 系统提示词 ==========

MULTIMODAL_SYSTEM_PROMPT = """你是一位专业的图片理解专家，擅长分析各种截图、图表、照片、界面等视觉内容。

你的工作：
1. 仔细观察用户提供的图片
2. 结合用户的问题，给出准确、详细的回答
3. 如果是报错截图，要指出：错误类型、关键错误信息、可能的原因、排查建议
4. 如果是图表/数据图，要解读数据、指出趋势和关键结论
5. 如果是界面截图，要描述界面布局和内容

工作原则：
- 基于图片事实回答，不要编造图片里没有的信息
- 回答结构清晰，分点说明
- 如果图片模糊或信息不足，直接告诉用户
"""

# 多模态 Agent 的系统提示词
# 注意：多模态模型的"看图"能力是模型自带的，提示词主要约束回答风格和结构

# ========== 多模态 Agent 类 ==========

class MultimodalAgent(BaseAgent):
    """
    多模态 Agent（图片理解专家）

    特别说明：
      多模态 Agent 和其它子 Agent 有一点不一样：
      它的输入不仅有 user_input（文字），还有图片（image_path / image_url / image_base64）
      所以 _arun / _run 接收参数时，要从 **kwargs 里取图片参数**

    为什么这么设计：
      - BaseAgent 的 _arun 签名是 (user_input, before_ctx, **kwargs)
      - 多模态需要的图片参数，通过 kwargs 透传进来
      - 保持基类契约不变，所有子 Agent 接口一致
    """

    def __init__(self):
        """构造函数"""
        super().__init__(name="multimodal")
        # name = "multimodal"，和注册表 key 一致
        # （get_agent("multimodal") 能拿到这个实例）

    # ===== 异步版本（主链路） =====

    async def _arun(self, user_input: str, before_ctx: dict = None, **kwargs) -> str:
        """
        【异步】执行多模态任务（图片理解）

        参数:
            user_input: 用户的文字问题/提示
            before_ctx: 中间件上下文
            **kwargs:   额外参数，其中可能包含：
                - image_path: 本地图片路径
                - image_url: 图片 URL
                - image_base64: base64 编码的图片

        返回:
            图片理解结果（文字回答）
        """
        # 从 kwargs 里提取图片参数
        image_path = kwargs.get("image_path")
        image_url = kwargs.get("image_url")
        image_base64 = kwargs.get("image_base64")
        # 三种图片输入方式，和 call_qwen_vl 的参数对应

        # 调用 Qwen-VL（注意：dashscope SDK 本身是同步的，所以用 asyncio.to_thread 包一层）
        import asyncio
        answer = await asyncio.to_thread(
            # asyncio.to_thread：把同步函数放到线程池里跑
            # 这样就不会阻塞事件循环，保持全异步语义
            call_qwen_vl,
            # 要跑的函数
            prompt=user_input,
            # 文字问题（就是 user_input）
            image_path=image_path,
            image_url=image_url,
            image_base64=image_base64,
            # 图片参数透传
        )
        # 为什么要用 to_thread：
        #   dashscope SDK 的 call 是同步阻塞的（等 API 返回才继续）
        #   如果直接在 async 函数里调用，会把整个事件循环卡住
        #   放到线程池里跑，主线程可以继续处理别的请求
        #   这是"同步 API 异步化"的标准做法

        return answer

    # ===== 同步版本（兜底） =====

    def _run(self, user_input: str, before_ctx: dict = None, **kwargs) -> str:
        """
        【同步】执行多模态任务

        参数:
            user_input: 用户的文字问题/提示
            before_ctx: 中间件上下文
            **kwargs:   图片参数（image_path / image_url / image_base64）

        返回:
            图片理解结果
        """
        image_path = kwargs.get("image_path")
        image_url = kwargs.get("image_url")
        image_base64 = kwargs.get("image_base64")

        return call_qwen_vl(
            prompt=user_input,
            image_path=image_path,
            image_url=image_url,
            image_base64=image_base64,
        )
        # 同步直接调用，不用 to_thread