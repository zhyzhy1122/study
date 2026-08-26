# -*- coding: utf-8 -*-
"""
src/api/deps.py
依赖与生命周期管理

作用：
  1. 应用启动时（lifespan）初始化工具注册中心（MCP server 等）
  2. 应用关闭时清理资源
  3. 提供"获取已初始化工具"的依赖函数

为什么单独放一个文件：
  初始化逻辑比较重（启动 MCP 子进程），不能每次请求都初始化一次。
  用 FastAPI 的 lifespan 机制，在应用启动时做一次，之后所有请求共享。
  这样第一次请求不会因为初始化而卡很久。
"""

# ========== 导入 ==========

import asyncio
# asyncio：标准库异步模块，用于给 MCP 预加载加超时

from contextlib import asynccontextmanager
# asynccontextmanager：异步上下文管理器装饰器
# 用来定义 FastAPI 的 lifespan（启动/关闭生命周期）

from src.modules.tools import init_tools, init_mcp_tools, get_tool_registry
# init_tools：初始化所有工具（注册 agent tools 等）
# init_mcp_tools：预加载 MCP 工具（Tavily / Playwright）
# get_tool_registry：拿到全局工具注册中心实例

# ========== 生命周期 ==========

@asynccontextmanager
async def lifespan(app):
    """
    FastAPI 应用生命周期

    参数:
        app: FastAPI 应用实例（虽然这里没用到，但签名要求）

    执行顺序:
        1. yield 之前的代码 → 应用启动时执行（只执行一次）
        2. yield → 交出控制权，应用开始接收请求
        3. yield 之后的代码 → 应用关闭时执行（清理资源）

    为什么用 lifespan 而不是 startup/shutdown 事件：
        FastAPI 新版推荐用 lifespan，替代了旧的 @app.on_event("startup")
        一个上下文管理器搞定启动+关闭，更清晰
    """
    print("\n" + "=" * 60)
    print("[API 启动] 正在初始化工具注册中心 ...")
    print("=" * 60)

    init_tools()
    # 初始化全部工具：注册所有 agent tools
    # 这一步比较重（要拉起子进程），所以放在启动时做

    try:
        # 预加载 MCP 工具并加超时，避免第一个搜索/学习路线请求卡在 npx 启动
        await asyncio.wait_for(init_mcp_tools(), timeout=15)
        print("[API 启动] MCP 工具预加载完成\n")
    except Exception as e:
        print(f"[API 启动] MCP 工具预加载失败（主服务继续运行）: {e}")

    print("[API 启动] 工具初始化完成，开始接收请求\n")

    try:
        yield
        # 交出控制权，应用开始正常服务请求
        # 所有请求处理都在这个 yield 期间发生
    finally:
        print("\n[API 关闭] 正在清理资源 ...")
        # 这里可以加 MCP server 关闭、数据库连接关闭等清理逻辑
        # 目前工具注册中心没有显式 close 方法，先留空
        print("[API 关闭] 清理完成\n")
