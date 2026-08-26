# -*- coding: utf-8 -*-
"""
src/agents/middleware/logging_middleware.py
作用：日志中间件，打印 Agent 的执行过程（开始/结束/耗时）
挂在哪：before_agent + after_agent（同步 + 异步双版本）
"""

# ========== 导入部分 ==========

import time
# time 模块：Python 标准库的时间模块
# 用来记录开始时间、计算耗时

from src.agents.base import BaseMiddleware
# 导入中间件基类，继承它

# ========== 日志中间件类 ==========

class LoggingMiddleware(BaseMiddleware):
    """
    日志中间件
    继承自 BaseMiddleware，重写 before_agent 和 after_agent
    同时提供同步和异步两个版本

    功能:
        1. Agent 开始时打印开始日志
        2. 记录开始时间
        3. Agent 结束时打印结束日志和耗时
    """

    def __init__(self):
        """构造函数：调用父类，设置中间件名称"""
        super().__init__(name="logging")
        # name 设为 "logging"，标识这是日志中间件

    # ===== 同步版本 =====

    def before_agent(self, agent_name: str, user_input: str, **kwargs) -> dict:
        """
        【同步】Agent 开始前调用

        参数:
            agent_name: Agent 名称
            user_input: 用户输入
            **kwargs:   额外参数

        返回:
            dict: 包含 start_time（开始时间戳）
        """

        start_time = time.time()
        # time.time() 返回当前时间戳（秒级，浮点数）

        # 打印开始日志
        print(f"\n{'='*60}")
        print(f"[Agent 开始] {agent_name}")
        print(f"输入内容: {user_input[:100]}{'...' if len(user_input) > 100 else ''}")
        print(f"{'='*60}")

        # 返回开始时间，放在 context 里传给后面
        return {"start_time": start_time}

    def after_agent(self, agent_name: str, result: str, **kwargs) -> dict:
        """
        【同步】Agent 结束后调用

        参数:
            agent_name: Agent 名称
            result:     Agent 的执行结果
            **kwargs:   额外参数（里面有 before_ctx）

        返回:
            dict: 包含 duration_ms（耗时毫秒）
        """

        # 从 kwargs 里取出 before_ctx
        before_ctx = kwargs.get("before_ctx", {})
        # before_ctx 是 before_agent 中间件返回的上下文

        start_time = before_ctx.get("start_time", time.time())
        # 从 before_ctx 里取开始时间

        end_time = time.time()
        # 记录结束时间

        duration_ms = round((end_time - start_time) * 1000, 2)
        # 计算耗时（毫秒）

        # 打印结束日志
        print(f"\n{'='*60}")
        print(f"[Agent 结束] {agent_name}")
        print(f"结果长度: {len(result)} 字符")
        print(f"耗时: {duration_ms} ms")
        print(f"{'='*60}\n")

        # 返回耗时
        return {"duration_ms": duration_ms}

    # ===== 异步版本 =====

    async def before_agent_async(self, agent_name: str, user_input: str, **kwargs) -> dict:
        """
        【异步】Agent 开始前调用
        和同步版本逻辑完全一样，只是用 async def 定义
        print 是同步的，但它很快，不影响性能
        """

        start_time = time.time()
        # 记录开始时间（time.time() 是同步的，但很快）

        print(f"\n{'='*60}")
        print(f"[Agent 开始] {agent_name}（异步）")
        print(f"输入内容: {user_input[:100]}{'...' if len(user_input) > 100 else ''}")
        print(f"{'='*60}")
        # 打印日志，标注"异步"方便区分

        return {"start_time": start_time}
        # 返回开始时间

    async def after_agent_async(self, agent_name: str, result: str, **kwargs) -> dict:
        """
        【异步】Agent 结束后调用
        和同步版本逻辑完全一样，只是用 async def 定义
        """

        # 从 kwargs 里取出 before_ctx
        before_ctx = kwargs.get("before_ctx", {})

        start_time = before_ctx.get("start_time", time.time())
        # 取开始时间

        end_time = time.time()
        # 记录结束时间

        duration_ms = round((end_time - start_time) * 1000, 2)
        # 计算耗时（毫秒）

        # 打印结束日志
        print(f"\n{'='*60}")
        print(f"[Agent 结束] {agent_name}（异步）")
        print(f"结果长度: {len(result)} 字符")
        print(f"耗时: {duration_ms} ms")
        print(f"{'='*60}\n")

        return {"duration_ms": duration_ms}
        # 返回耗时

# ========== 文件结束 ==========