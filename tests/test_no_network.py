# -*- coding: utf-8 -*-
"""
tests/test_no_network.py
"测试必须完全离线"的守卫测试

做法：把 socket.socket 换成"一被创建就抛异常"的桩，
然后跑一遍有代表性的异步流程（重写器 + 记忆中间件 + 反思中间件 + 数据层）。
只要有任何代码尝试建立网络连接，测试就会立刻失败。

注意：TestClient 走的是 ASGI 内存传输，不创建真实 socket，
所以在 test_api.py 里跑 HTTP 冒烟测试并不需要联网。
"""

from __future__ import annotations

import json
import socket
import sqlite3

import pytest

import src.agents.middleware.memory_middleware as memory_mw
import src.agents.middleware.reflection_middleware as reflection_mw
from src.agents.middleware.memory_middleware import MemoryMiddleware
from src.agents.middleware.reflection_middleware import ReflectionMiddleware
from src.modules import rewriter
from src.modules.rewriter.agent import rewrite_input

from conftest import run_async


class NetworkAccessAttempted(AssertionError):
    """测试期间发生了真实网络访问。"""


@pytest.fixture
def forbid_network(monkeypatch):
    """
    拦截所有"向外发起连接"的动作。

    分两层拦：
      1. socket.getaddrinfo —— 任何真实网络访问（含域名解析）都必经这一步；
         本地回环连接（asyncio 的自唤醒管道）不需要解析，因此不会误报。
      2. asyncio 事件循环的 client 连接方法 —— 保证没人能绕过第 1 层。

    注意：不能直接把 socket.socket 或它的 connect 换掉，那样会连 asyncio 在
    Windows 上建自唤醒管道（socketpair）也一起打断，属于误伤。
    """
    import asyncio

    monkeypatch.setattr(socket, "getaddrinfo", _raise_network, raising=True)

    loop = asyncio.get_event_loop_policy().new_event_loop()
    try:
        loop_cls = type(loop)
    finally:
        loop.close()

    monkeypatch.setattr(
        loop_cls, "_create_connection_transport", _raise_network_transport, raising=False
    )
    yield


def _raise_network(*args, **kwargs):
    raise NetworkAccessAttempted("测试期间尝试发起网络连接")


def _raise_network_transport(self, *args, **kwargs):
    raise NetworkAccessAttempted("测试期间 asyncio 尝试建立网络连接")


JUDGE_OUTPUT = json.dumps(
    {
        "scores": [
            {"dimension": d, "score": 5, "reason": "ok"}
            for d in ["Accuracy", "Completeness", "Clarity", "Helpfulness", "Safety"]
        ],
        "total": 25,
        "passed": True,
        "suggestion": "",
    },
    ensure_ascii=False,
)


def test_rewriter_and_middlewares_run_without_any_socket(patch_llm, forbid_network):
    # 把三处 LLM 工厂都换成假模型（定义处打补丁，覆盖延迟 import 的场景）
    patch_llm(None, ['{"should_update": true, "profile": {"goal": "学 AI"}}'])
    patch_llm(rewriter.agent, ["补全后的问题"])
    patch_llm(reflection_mw, [JUDGE_OUTPUT])

    async def scenario():
        rewritten = await rewrite_input("s-offline", "D")
        await MemoryMiddleware(user_id="offline").after_agent_async(
            "echo", "回答", user_input="你好", before_ctx={}
        )
        report = ReflectionMiddleware()._judge("echo", rewritten, "回答")
        return rewritten, report

    rewritten, report = run_async(scenario())

    assert rewritten == "补全后的问题"
    assert report.passed is True
    assert report.total == 25


def test_sqlite_layers_do_not_need_network(forbid_network):
    from src.memory.store import get_all_memories, save_memory

    run_async(save_memory("user:offline", "profile", {"level": "零基础"}))
    assert run_async(get_all_memories("user:offline")) == {"profile": {"level": "零基础"}}
    assert sqlite3.sqlite_version  # 纯本地库，不需要任何连接


def test_guard_actually_catches_outbound_connections(forbid_network):
    """自检：守卫本身必须真的能拦住外连，否则它只是摆设。"""
    with pytest.raises(NetworkAccessAttempted):
        socket.getaddrinfo("example.com", 80)

    with pytest.raises(NetworkAccessAttempted):
        socket.create_connection(("example.com", 80), timeout=0.01)
