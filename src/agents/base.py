# -*- coding: utf-8 -*-
"""
src/agents/base.py
作用：Agent 的基础工具模块，所有 Agent 共用的东西放这里
内容：
  1. 执行计划的数据结构（PlanStep / ExecutionPlan）
  2. 中间件基础（BaseMiddleware / MiddlewareManager）
     - 同步 + 异步双版本
  3. Agent 基类（BaseAgent，带中间件支持）
     - run() 同步调用
     - arun() 异步调用
  4. Agent 注册表（AGENT_REGISTRY / get_agent）
"""

# ========== 导入部分 ==========

import asyncio
# asyncio：Python 标准库的异步编程模块
# 提供 async/await 语法支持，用来写异步代码

from typing import List, Literal, Optional, Dict, Any
# List: 列表类型
# Literal: 只能取指定值
# Optional: 可选（可以为 None）
# Dict: 字典类型
# Any: 任意类型

from pydantic import BaseModel
# pydantic 数据验证库
# ========== 1. 执行计划的数据结构 ==========

class PlanStep(BaseModel):
    """
    执行计划的一个步骤

    属性:
        agent:       要调用的 Agent 名称
        action:      该步骤的动作描述（可选）
        description: 人类可读的步骤描述
    """
    agent: str
    action: Optional[str] = None
    description: str = ""

class ExecutionPlan(BaseModel):
    """
    完整的执行计划

    属性:
        intent:       用户意图分类
        steps:        执行步骤列表
        mode:         执行模式（serial 串行 / parallel 并行）
        summary_hint: 汇总时的提示词
    """
    intent: Literal["code_review", "learning_path", "mixed", "other"]
    steps: List[PlanStep]
    mode: Literal["serial", "parallel"] = "serial"
    summary_hint: str = ""

# ========== 2. 中间件基础 ==========

class BaseMiddleware:
    """
    中间件基类
    所有中间件都继承这个类，重写需要的钩子方法

    4 个钩子（按执行顺序）：
        before_agent:  Agent 开始执行前
        before_llm:    调大模型前
        after_llm:     调大模型后
        after_agent:   Agent 执行完后

    同步 vs 异步：
        - 同步钩子（不带 _async 后缀）：同步 Agent 调用
        - 异步钩子（带 _async 后缀）：异步 Agent 调用
        - 子类按需重写，不需要的就不重写

    每个方法都返回 dict（或 None），返回的内容会被传给下一个中间件
    """

    def __init__(self, name: str):
        """
        构造函数

        参数:
            name: 中间件的名称（用于日志和调试）
        """
        self.name = name
        # name 属性：中间件的名字
        # 每个中间件有唯一名字，方便识别和调试

    # ===== 同步版本的钩子 =====

    def before_agent(self, agent_name: str, user_input: str, **kwargs) -> Optional[Dict[str, Any]]:
        """
        【同步】Agent 开始执行前调用

        参数:
            agent_name:  Agent 的名称
            user_input:  用户输入
            **kwargs:    额外参数

        返回:
            可选的字典，里面可以放一些上下文数据
            返回 None 表示不做任何修改
        """
        return None  # 默认不做任何事，子类重写

    def before_llm(self, agent_name: str, prompt: str, **kwargs) -> Optional[Dict[str, Any]]:
        """
        【同步】调大模型前调用

        参数:
            agent_name: Agent 名称
            prompt:     传给模型的提示词
            **kwargs:   额外参数

        返回:
            可选字典
        """
        return None

    def after_llm(self, agent_name: str, response: str, **kwargs) -> Optional[Dict[str, Any]]:
        """
        【同步】调大模型后调用

        参数:
            agent_name: Agent 名称
            response:   模型返回的结果
            **kwargs:   额外参数

        返回:
            可选字典
        """
        return None

    def after_agent(self, agent_name: str, result: str, **kwargs) -> Optional[Dict[str, Any]]:
        """
        【同步】Agent 执行完后调用

        参数:
            agent_name: Agent 名称
            result:     Agent 的最终结果
            **kwargs:   额外参数

        返回:
            可选字典
        """
        return None

    # ===== 异步版本的钩子 =====

    async def before_agent_async(self, agent_name: str, user_input: str, **kwargs) -> Optional[Dict[str, Any]]:
        """
        【异步】Agent 开始执行前调用
        和同步版本功能一样，只是用 async def 定义
        """
        return None

    async def before_llm_async(self, agent_name: str, prompt: str, **kwargs) -> Optional[Dict[str, Any]]:
        """【异步】调大模型前调用"""
        return None

    async def after_llm_async(self, agent_name: str, response: str, **kwargs) -> Optional[Dict[str, Any]]:
        """【异步】调大模型后调用"""
        return None

    async def after_agent_async(self, agent_name: str, result: str, **kwargs) -> Optional[Dict[str, Any]]:
        """【异步】Agent 执行完后调用"""
        return None

class MiddlewareManager:
    """
    中间件管理器
    负责注册中间件、按顺序调用所有中间件的钩子

    用法:
        manager = MiddlewareManager()
        manager.add(LogMiddleware())
        manager.run_before_agent("code_review", "用户输入")
        await manager.run_before_agent_async("code_review", "用户输入")
    """

    def __init__(self):
        """构造函数：初始化中间件列表"""
        self.middlewares: List[BaseMiddleware] = []
        # middlewares 列表：存所有注册的中间件
        # 按注册顺序执行（先注册的先执行 before，后执行 after）

    def add(self, middleware: BaseMiddleware):
        """
        注册一个中间件

        参数:
            middleware: 中间件实例（BaseMiddleware 的子类）
        """
        self.middlewares.append(middleware)
        # 把中间件加到列表末尾
        # 注册顺序就是执行顺序

    # ===== 同步版本的执行方法 =====

    def run_before_agent(self, agent_name: str, user_input: str, **kwargs) -> Dict[str, Any]:
        """
        【同步】执行所有中间件的 before_agent 钩子

        参数:
            agent_name: Agent 名称
            user_input: 用户输入

        返回:
            所有中间件返回的数据合并后的字典
        """
        context = {}
        # context 字典：收集所有中间件返回的数据
        # 每个中间件返回的东西都会合并到这里面

        for mw in self.middlewares:
            # 遍历所有中间件
            try:
                result = mw.before_agent(agent_name, user_input, **kwargs)
                # 调用中间件的 before_agent 方法
                if result and isinstance(result, dict):
                    # 如果返回了字典，就合并到 context 里
                    context.update(result)
            except Exception as e:
                # 中间件出错不影响主流程，打印错误继续
                print(f"[中间件错误] {mw.name}.before_agent: {e}")

        return context
        # 返回合并后的上下文

    def run_before_llm(self, agent_name: str, prompt: str, **kwargs) -> Dict[str, Any]:
        """【同步】执行所有中间件的 before_llm 钩子"""
        context = {}
        for mw in self.middlewares:
            try:
                result = mw.before_llm(agent_name, prompt, **kwargs)
                if result and isinstance(result, dict):
                    context.update(result)
            except Exception as e:
                print(f"[中间件错误] {mw.name}.before_llm: {e}")
        return context

    def run_after_llm(self, agent_name: str, response: str, **kwargs) -> Dict[str, Any]:
        """【同步】执行所有中间件的 after_llm 钩子"""
        context = {}
        for mw in self.middlewares:
            try:
                result = mw.after_llm(agent_name, response, **kwargs)
                if result and isinstance(result, dict):
                    context.update(result)
            except Exception as e:
                print(f"[中间件错误] {mw.name}.after_llm: {e}")
        return context

    def run_after_agent(self, agent_name: str, result: str, **kwargs) -> Dict[str, Any]:
        """【同步】执行所有中间件的 after_agent 钩子"""
        context = {}
        for mw in self.middlewares:
            try:
                result_mw = mw.after_agent(agent_name, result, **kwargs)
                if result_mw and isinstance(result_mw, dict):
                    context.update(result_mw)
            except Exception as e:
                print(f"[中间件错误] {mw.name}.after_agent: {e}")
        return context

    # ===== 异步版本的执行方法 =====

    async def run_before_agent_async(self, agent_name: str, user_input: str, **kwargs) -> Dict[str, Any]:
        """
        【异步】执行所有中间件的 before_agent 钩子
        和同步版本逻辑一样，只是用 await 调用异步钩子
        """
        context = {}
        for mw in self.middlewares:
            try:
                result = await mw.before_agent_async(agent_name, user_input, **kwargs)
                # await 调用异步钩子方法
                if result and isinstance(result, dict):
                    context.update(result)
            except Exception as e:
                print(f"[中间件错误] {mw.name}.before_agent_async: {e}")
        return context

    async def run_before_llm_async(self, agent_name: str, prompt: str, **kwargs) -> Dict[str, Any]:
        """【异步】执行所有中间件的 before_llm 钩子"""
        context = {}
        for mw in self.middlewares:
            try:
                result = await mw.before_llm_async(agent_name, prompt, **kwargs)
                if result and isinstance(result, dict):
                    context.update(result)
            except Exception as e:
                print(f"[中间件错误] {mw.name}.before_llm_async: {e}")
        return context

    async def run_after_llm_async(self, agent_name: str, response: str, **kwargs) -> Dict[str, Any]:
        """【异步】执行所有中间件的 after_llm 钩子"""
        context = {}
        for mw in self.middlewares:
            try:
                result = await mw.after_llm_async(agent_name, response, **kwargs)
                if result and isinstance(result, dict):
                    context.update(result)
            except Exception as e:
                print(f"[中间件错误] {mw.name}.after_llm_async: {e}")
        return context

    async def run_after_agent_async(self, agent_name: str, result: str, **kwargs) -> Dict[str, Any]:
        """【异步】执行所有中间件的 after_agent 钩子"""
        context = {}
        for mw in self.middlewares:
            try:
                result_mw = await mw.after_agent_async(agent_name, result, **kwargs)
                if result_mw and isinstance(result_mw, dict):
                    context.update(result_mw)
            except Exception as e:
                print(f"[中间件错误] {mw.name}.after_agent_async: {e}")
        return context

# ========== 3. Agent 基类（带中间件支持 + 同步/异步双版本） ==========

class BaseAgent:
    """
    Agent 基类（带中间件支持）
    所有专业 Agent 都继承这个类

    属性：
        name:        Agent 的名称
        middleware:  中间件管理器

    方法：
        run(user_input):  同步执行入口
        _run(user_input): 同步业务逻辑（子类重写）
        arun(user_input): 异步执行入口
        _arun(user_input):异步业务逻辑（子类重写）
    """

    def __init__(self, name: str):
        """
        构造函数

        参数:
            name: Agent 的名称
        """
        self.name = name
        # name 属性：Agent 名称

        self.middleware = MiddlewareManager()
        # middleware 属性：中间件管理器实例
        # 每个 Agent 有自己的中间件管理器

    # ===== 同步版本 =====

    def run(self, user_input: str, max_reflect: int = 3, **kwargs) -> str:
        """
        【同步】Agent 的执行入口（带中间件 + 反思打回重跑）
        这是"模板方法"，固定执行顺序，子类不用重写
        子类只需要重写 _run 方法

        参数:
            user_input:   用户输入
            max_reflect:  最多打回重跑次数（默认 3）
                         满 max_reflect 次仍不达标 → 直接释放最后一次结果，不再重试
            **kwargs:    额外参数

        返回:
            Agent 的执行结果（字符串）

        打回重跑逻辑：
            跑完 → 评估 → 不达标且有建议 → 带建议重跑 → 再评估……
            达标 或 次数用尽 或 无建议 → 停止并释放当前结果
        """

        # 钩子 1：before_agent（Agent 开始前）
        before_ctx = self.middleware.run_before_agent(self.name, user_input, **kwargs)
        # 先执行 before 中间件（如日志开始/计时起点/读取记忆）

        # 把中期记忆（MemoryMiddleware 读到的 memory_context）拼进 user_input
        memory_text = before_ctx.get("memory_context", "")
        # 从 before_ctx 取记忆文本（MemoryMiddleware.before_agent_async 放入）
        # 没有记忆中间件时 get 到空字符串，不影响原流程

        if memory_text:
            # 只有确实有历史记忆时才拼接
            user_input = f"{user_input}\n\n{memory_text}"
            # 把历史记忆追加到用户输入后面
            # 同步/异步保持一致，记忆都能进入 Agent 上下文

        # 真正的业务逻辑（子类实现）
        result = self._run(user_input, before_ctx=before_ctx, **kwargs)
        # 第一次执行业务逻辑

        # ===== 反思评估 + 打回重跑循环（与异步版同构） =====
        for _ in range(max_reflect):
            # for 最多跑 max_reflect 轮（默认 3）
            # 达到 max_reflect 次重跑仍不达标时，for 耗尽 → 自动跳出 → 释放最后一次结果

            # 钩子 4：after_agent（评估这一轮的结果）
            after_ctx = self.middleware.run_after_agent(
                self.name, result, before_ctx=before_ctx, user_input=user_input, **kwargs
            )
            # 反射中间件打 5 维分，把 eval_report 放进 after_ctx
            # 传原始 user_input：让裁判拿"原始问题"比对

            report = after_ctx.get("eval_report")
            # 取出评估报告；没挂评估中间件时 get 到 None

            if report is not None and not report.passed and report.suggestion:
                # 打回重跑条件：不达标 且 有改进建议
                improved_input = user_input + "\n\n【评估反馈，请按以下建议改进后重新完整输出】\n" + report.suggestion
                # 把裁判建议拼进输入，作为对 Agent 的补充要求
                # 业务 Agent 从 user_input 构造提示词，追加后自动朝改进方向重写

                result = self._run(improved_input, before_ctx=before_ctx, **kwargs)
                # 用改进后的输入重跑一次
                continue
                # 进入下一轮评估新结果

            # 达标 或 无建议 或 中间件返回了 new_result
            if "new_result" in after_ctx:
                # 兼容其它中间件主动改结果
                result = after_ctx["new_result"]
            break
            # 退出循环，释放当前结果

        return result
        # 满 max_reflect 次仍不达标时，for 耗尽自动到这里 → 释放最后一次结果
        # 这就是"三次不达标就释放"的同步版实现点

    def _run(self, user_input: str, before_ctx: dict = None, **kwargs) -> str:
        """
        【同步】真正的业务逻辑（子类必须重写）

        参数:
            user_input:  用户输入
            before_ctx:  before_agent 中间件返回的上下文
            **kwargs:    额外参数

        返回:
            执行结果字符串
        """
        raise NotImplementedError(f"Agent {self.name} 没有实现 _run 方法")

    # ===== 异步版本 =====

    async def arun(self, user_input: str, max_reflect: int = 3, **kwargs) -> str:
        """
        【异步】Agent 的执行入口（带中间件 + 反思打回重跑）

        参数:
            user_input:   用户输入
            max_reflect:  最多打回重跑次数（默认 3）
                         满 max_reflect 次仍不达标 → 直接释放最后一次结果，不再重试
            **kwargs:    额外参数

        返回:
            Agent 的执行结果（字符串）

        打回重跑逻辑：
            跑完 → 评估 → 不达标且有建议 → 带建议重跑 → 再评估……
            达标 或 次数用尽 或 无建议 → 停止并释放当前结果
        """

        # 钩子 1：before_agent_async（异步）
        before_ctx = await self.middleware.run_before_agent_async(self.name, user_input, **kwargs)
        # 异步执行 before 中间件（启动日志/计时、读取记忆等）

        # 把中期记忆（MemoryMiddleware 读到的 memory_context）拼进 user_input
        memory_text = before_ctx.get("memory_context", "")
        # 从 before_ctx 取记忆文本（MemoryMiddleware.before_agent_async 放入）
        # 没有记忆中间件时 get 到空字符串，不影响原流程

        if memory_text:
            # 只有确实有历史记忆时才拼接，避免污染无记忆场景
            user_input = f"{user_input}\n\n{memory_text}"
            # 把历史记忆追加到用户输入后面
            # 这样 _arun 用 user_input 构造提示词时，历史记忆自然进入 Agent 上下文
            # 语义："这是你想学的内容 + 这是这个用户之前的情况，请结合回答"

        # 真正的异步业务逻辑（子类实现）
        result = await self._arun(user_input, before_ctx=before_ctx, **kwargs)
        # 第一次执行业务逻辑

        # ===== 异步版的反思评估 + 打回重跑循环 =====
        for _ in range(max_reflect):
            # for 最多跑 max_reflect 轮（默认 3 轮）
            # 每轮：先评估当前结果 → 决定是否重跑 → 重跑后 continue 进下一轮评估
            # 当 for 的轮数耗尽（即重跑满了 max_reflect 次）时，自动跳出循环 → 释放

            after_ctx = await self.middleware.run_after_agent_async(
                self.name, result, before_ctx=before_ctx, user_input=user_input, **kwargs
            )
            # 异步评估这一轮的结果：反射中间件打 5 维分，把 eval_report 放进 after_ctx
            # 注意这里传原始 user_input，让裁判拿"原始问题"比对，而不是在读带反馈的输入

            report = after_ctx.get("eval_report")
            # 取出评估报告；没有挂评估中间件时 get 到 None

            if report is not None and not report.passed and report.suggestion:
                # 打回重跑条件：有评估报告 且 不达标 且 有改进建议
                improved_input = user_input + "\n\n【评估反馈，请按以下建议改进后重新完整输出】\n" + report.suggestion
                # 把裁判建议拼进输入，作为对 Agent 的"补充要求"
                # 业务 Agent 从 user_input 构造提示词，追加后会自动朝改进方向重写

                result = await self._arun(improved_input, before_ctx=before_ctx, **kwargs)
                # 用改进后的输入重跑一次业务逻辑
                continue
                # 进入下一轮，重新评估新结果（直到达标或满 max_reflect 次）

            # 走到这里说明：达标 或 没有建议 或 中间件返回了 new_result
            if "new_result" in after_ctx:
                # 兼容其它中间件主动改结果
                result = after_ctx["new_result"]
            break
            # 无论哪种都退出循环，不再重试 → 释放当前结果

        return result
        # 【关键】满 max_reflect 次仍不达标时，循环自然结束，
        # 不会无限重跑，直接把"最后一次结果"返回给上层（释放）
        # 这是"三次不达标就释放"的实现点

    async def _arun(self, user_input: str, before_ctx: dict = None, **kwargs) -> str:
        """
        【异步】真正的业务逻辑（子类可以重写，也可以不重写）
        默认实现：调用同步的 _run（兼容那些只实现了同步的子类）
        子类如果有异步逻辑，就重写这个方法

        参数:
            user_input:  用户输入
            before_ctx:  before_agent 中间件返回的上下文
            **kwargs:    额外参数

        返回:
            执行结果字符串
        """
        # 默认实现：用 asyncio.to_thread 把同步 _run 放到线程里跑
        # 这样即使子类没实现 _arun，异步调用也不会报错
        return await asyncio.to_thread(self._run, user_input, before_ctx=before_ctx, **kwargs)
        # asyncio.to_thread(func, *args)：把同步函数放到线程池中执行
        # 返回一个 awaitable 对象，可以 await
        # 作用：让同步代码也能在异步环境里跑，不会阻塞事件循环


# ========== 4. Agent 注册表 + 默认中间件 ==========
# 真实 Agent 在 src/modules/ 下面，这里只是注册入口
# 用懒加载方式：第一次 get_agent 时才实例化，并挂载默认中间件

AGENT_REGISTRY = {}
# AGENT_REGISTRY：Agent 注册表
# key 是 Agent 名称，value 是 Agent 实例
# 初始为空，懒加载


def setup_agent_middlewares(agent: BaseAgent):
    """
    给 Agent 挂载默认中间件
    所有 Agent 创建后都会调用这个函数，统一配置中间件

    参数:
        agent: Agent 实例

    当前默认中间件：
        1. LoggingMiddleware：打印执行日志（开始/结束/耗时）

    以后加评估中间件、错误处理中间件，都在这里加
    """

    from src.agents.middleware.logging_middleware import LoggingMiddleware
    # 延迟导入，避免循环导入
    # 因为 logging_middleware 里导入了 BaseMiddleware，base.py 里也有
    # 放在函数里面导入，就不会循环了

    from src.agents.middleware.reflection_middleware import ReflectionMiddleware
    # 延迟导入评估中间件（和上面 LoggingMiddleware 同理，避免循环导入）
    # reflection_middleware.py 里 import 了 BaseMiddleware，所以要在函数内导入

    from src.agents.middleware.memory_middleware import MemoryMiddleware
    # 延迟导入记忆中间件（同样避免循环导入）
    # memory_middleware 里 import 了 BaseMiddleware 和 store，函数内导入最安全

    agent.middleware.add(LoggingMiddleware())
    # 给 Agent 加上日志中间件

    agent.middleware.add(MemoryMiddleware())
    # 给 Agent 挂上长期记忆中间件
    # 作用：每次 Agent 执行前，读取该用户历史记忆放入 before_ctx
    #       BaseAgent.arun() 会把记忆拼进 user_input，让 Agent 记得用户
    # 挂载顺序：logging → memory → reflection
    # 因为 before_agent 钩子按注册顺序执行：logging 先打开始日志，memory 再读记忆

    agent.middleware.add(ReflectionMiddleware())
    # 给 Agent 再挂上反思评估中间件
    # 挂载顺序：先 logging + memory(in before)，后 reflection(in after)
    # 因为两者都只挂 after_agent，执行时按注册顺序：
    #   logging 先打印"Agent 结束/耗时"，reflection 再打分

def get_agent(agent_name: str) -> BaseAgent:
    """
    根据 Agent 名称从注册表获取实例（懒加载 + 自动挂载默认中间件）

    参数:
        agent_name: Agent 名称

    返回:
        Agent 实例

    异常:
        ValueError: 未知的 Agent 名称
    """

    if agent_name not in AGENT_REGISTRY:
        # 如果注册表中没有，就按需创建
        if agent_name == "code_review":
            from src.modules.code_review.agent import CodeReviewAgent
            agent = CodeReviewAgent()
        elif agent_name == "learning_path":
            from src.modules.learning_path.agent import LearningPathAgent
            agent = LearningPathAgent()
        elif agent_name == "search":
            from src.modules.search.agent import SearchAgent
            agent = SearchAgent()
        elif agent_name == "multimodal":
            from src.modules.multimodal.agent import MultimodalAgent
            agent = MultimodalAgent()
        else:
            raise ValueError(f"未知的 Agent: {agent_name}，可用的有: code_review, learning_path, search")

        # 创建完后，自动挂载默认中间件
        setup_agent_middlewares(agent)

        # 注册到注册表
        AGENT_REGISTRY[agent_name] = agent

    return AGENT_REGISTRY[agent_name]
    # 返回 Agent 实例
# ========== 文件结束 ==========