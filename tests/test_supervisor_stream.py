# -*- coding: utf-8 -*-
"""
tests/test_supervisor_stream.py
总控流式输出（src/agents/supervisor.py::astream_supervisor）的离线测试

为什么单独测：astream_supervisor 不只是"转发事件"，它还做三件业务判断：
  1. 把 on_tool_start / on_tool_end 翻译成前端能显示的工具卡片
  2. 把 on_chain_start 翻译成"正在规划学习路线…"这类思考提示
  3. 把模型输出里的"内部 JSON"（记忆更新 / 反思评分）整块吞掉，绝不外泄给前端
第 3 条最容易被改坏，所以这里用假事件流覆盖三种到达形态：
整块 JSON、跨 chunk 的 JSON、以及"闭括号与正文挤在同一个 chunk"的缺陷现状。
"""

from __future__ import annotations

from langchain_core.messages import AIMessageChunk

import src.agents.supervisor as supervisor_mod

from conftest import run_async


class FakeEventGraph:
    """按脚本产出 LangGraph astream_events 风格的事件。"""

    def __init__(self, events):
        self._events = events

    async def astream_events(self, payload, version=None, **kwargs):
        for event in self._events:
            yield event


def chunk(text: str) -> dict:
    return {
        "event": "on_chat_model_stream",
        "name": "ChatOpenAI",
        "data": {"chunk": AIMessageChunk(content=text)},
    }


def collect(monkeypatch, events) -> list[dict]:
    monkeypatch.setattr(
        supervisor_mod, "build_supervisor_agent", lambda *a, **k: FakeEventGraph(events)
    )

    async def scenario():
        return [e async for e in supervisor_mod.astream_supervisor("问题")]

    return run_async(scenario())


def test_stream_yields_tokens_then_done(monkeypatch):
    out = collect(monkeypatch, [chunk("你"), chunk("好"), chunk("呀")])

    assert out == [
        {"type": "token", "content": "你"},
        {"type": "token", "content": "好"},
        {"type": "token", "content": "呀"},
        {"type": "done"},
    ]


def test_stream_translates_tool_events_into_frontend_cards(monkeypatch):
    events = [
        {
            "event": "on_tool_start",
            "name": "search_expert",
            "data": {"input": {"query": "PyTorch 2.0 新特性"}},
        },
        {
            "event": "on_tool_end",
            "name": "search_expert",
            "data": {"output": "结果" * 60},
        },
    ]

    out = collect(monkeypatch, events)

    assert out[0] == {
        "type": "tool_start",
        "tool": "search_expert",
        "content": "PyTorch 2.0 新特性",
    }
    assert out[1]["type"] == "tool_end"
    assert out[1]["tool"] == "search_expert"
    assert out[1]["content"].endswith("...")  # 长输出被截断成摘要
    assert out[1]["content"].startswith("结果")


def test_stream_tool_end_with_non_string_output_uses_placeholder(monkeypatch):
    events = [{"event": "on_tool_end", "name": "code_review_expert", "data": {"output": {"ok": True}}}]

    out = collect(monkeypatch, events)

    assert out[0] == {"type": "tool_end", "tool": "code_review_expert", "content": "完成"}


def test_stream_emits_thinking_phase_for_meaningful_chain_names(monkeypatch):
    events = [
        {"event": "on_chain_start", "name": "learning_path", "data": {}},
        {"event": "on_chain_start", "name": "code_review", "data": {}},
        {"event": "on_chain_start", "name": "some_internal_helper", "data": {}},
    ]

    out = collect(monkeypatch, events)

    assert [e["content"] for e in out if e["type"] == "thinking"] == [
        "正在规划学习路线...",
        "正在分析代码...",
    ]
    assert out[-1] == {"type": "done"}


def test_stream_swallows_internal_json_delivered_as_one_chunk(monkeypatch):
    """记忆中间件/反思评估的 JSON 是内部数据，绝不能流到前端。"""
    internal = '{"should_update": true, "profile": {"level": "零基础"}}'
    events = [chunk("这是回答。"), chunk(internal)]

    out = collect(monkeypatch, events)

    tokens = [e["content"] for e in out if e["type"] == "token"]
    assert tokens == ["这是回答。"]
    assert all("should_update" not in t for t in tokens)


def test_stream_swallows_json_split_across_chunks(monkeypatch):
    """JSON 逐字符流式到达时，闭括号那一片到达即判定闭合，整块丢弃，之后文本正常放行。"""
    events = [chunk("正文")]
    for piece in ['{"scores": [', '{"dimension": "Accuracy", "score": 5}', "], ", '"total": 25', "}"]:
        events.append(chunk(piece))
    events.append(chunk("收尾"))

    out = collect(monkeypatch, events)

    tokens = [e["content"] for e in out if e["type"] == "token"]
    assert tokens == ["正文", "收尾"]


def test_stream_json_buffer_keeps_swallowing_text_after_closing_brace(monkeypatch):
    """
    已知缺陷（当前行为，非期望行为）：吞噬器只在"缓冲内容恰好以 } 结尾"时才判定闭合。
    如果内部 JSON 的闭括号与紧随其后的正文挤在同一个 chunk 里，
    缓冲区会变成 '{"a": 1}结束。'，既不闭合也无法解析 → 后续正文被一直扣在缓冲里，
    最终不会推给前端（表现为回答"说一半没了"）。
    这里锁定现状，避免以后无意改坏；真正修复需要改成"按大括号配对"扫描。
    """
    events = [chunk("开头"), chunk('{"should_update": true}结束。')]

    out = collect(monkeypatch, events)

    tokens = [e["content"] for e in out if e["type"] == "token"]
    assert tokens == ["开头"]  # "结束。" 被吞掉（缺陷现状）


def test_stream_keeps_plain_braces_text(monkeypatch):
    """普通文本里的花括号（例如集合字面量）不以 { 开头，不应被误吞。"""
    events = [chunk("def f():\n    return {1, 2}")]

    out = collect(monkeypatch, events)

    assert out[0]["content"] == "def f():\n    return {1, 2}"


def test_stream_skips_empty_and_non_text_chunks(monkeypatch):
    events = [
        {"event": "on_chat_model_stream", "name": "m", "data": {"chunk": None}},
        {"event": "on_chat_model_stream", "name": "m", "data": {"chunk": AIMessageChunk(content="")}},
        {"event": "on_chat_model_end", "name": "m", "data": {}},
    ]

    out = collect(monkeypatch, events)

    assert out == [{"type": "done"}]
