# -*- coding: utf-8 -*-
"""
tests/test_rewriter.py
输入重写器（src/modules/rewriter/agent.py）的离线测试

为什么值得单独测：它是"总控之前"的第一道处理，直接决定用户一句"D"能不能
被正确补全。它有三条明确的短路规则和一条异常兜底，都属于"错一点就影响体验"。
全部用假 LLM，且用 tmp 的 messages.db 提供历史上下文。
"""

from __future__ import annotations

from src.memory.messages import add_message
from src.modules import rewriter
from src.modules.rewriter.agent import (
    HISTORY_LIMIT,
    SHORT_THRESHOLD,
    _build_rewrite_prompt,
    async_input_short,
    async_is_trial_shorthand,
    rewrite_input,
)

from conftest import run_async


# ===========================================================================
# 纯判断逻辑（不发 LLM）
# ===========================================================================

def test_async_input_short_threshold_boundary():
    assert async_input_short("D") is True
    assert async_input_short("x" * SHORT_THRESHOLD) is True
    assert async_input_short("x" * (SHORT_THRESHOLD + 1)) is False
    assert async_input_short("   短句   ") is True  # 首尾空白不计入长度


def test_async_is_trial_shorthand_matches_greetings_case_insensitively():
    assert async_is_trial_shorthand("你好") is True
    assert async_is_trial_shorthand("  Hello  ") is True
    assert async_is_trial_shorthand("AI") is True
    assert async_is_trial_shorthand("谢谢") is True
    assert async_is_trial_shorthand("我想学 Python") is False


# ===========================================================================
# 提示词组装
# ===========================================================================

def test_build_rewrite_prompt_includes_history_and_current_message():
    run_async(add_message("sess-rw", "user", "我想学 C++"))
    run_async(add_message("sess-rw", "ai", "你选 A前端/B后端/C数据分析？"))

    prompt = run_async(_build_rewrite_prompt("sess-rw", "D"))

    assert "【对话历史】" in prompt
    assert "用户: 我想学 C++" in prompt
    assert "AI: 你选 A前端/B后端/C数据分析？" in prompt
    assert "【用户当前输入】\nD" in prompt


def test_build_rewrite_prompt_marks_missing_history():
    prompt = run_async(_build_rewrite_prompt("sess-empty", "嗯"))

    assert "（暂无历史，这是第一句）" in prompt


def test_build_rewrite_prompt_only_reads_the_given_session():
    run_async(add_message("sess-a", "user", "A 会话的历史"))
    run_async(add_message("sess-b", "user", "B 会话的历史"))

    prompt = run_async(_build_rewrite_prompt("sess-a", "然后呢"))

    assert "A 会话的历史" in prompt
    assert "B 会话的历史" not in prompt


def test_build_rewrite_prompt_limits_history_length():
    for i in range(HISTORY_LIMIT + 3):
        run_async(add_message("sess-limit", "user", f"历史{i}"))

    prompt = run_async(_build_rewrite_prompt("sess-limit", "继续"))

    assert f"历史{HISTORY_LIMIT + 2}" in prompt  # 最新的在
    assert "历史0" not in prompt  # 更早的被截掉


# ===========================================================================
# rewrite_input：三条短路 + LLM 补全 + 异常兜底
# ===========================================================================

def test_rewrite_input_returns_long_message_untouched_without_llm(patch_llm):
    fake = patch_llm(rewriter.agent, ["不应该被调用"])
    long_message = "请帮我规划一条从零基础到能独立开发 AI 应用的完整学习路线"

    assert run_async(rewrite_input("s1", long_message)) == long_message
    assert fake.calls == []  # 长句直接放行，省一次调用


def test_rewrite_input_skips_trial_shorthand_without_llm(patch_llm):
    fake = patch_llm(rewriter.agent, ["不应该被调用"])

    assert run_async(rewrite_input("s1", "你好")) == "你好"
    assert run_async(rewrite_input("s1", "AI")) == "AI"
    assert fake.calls == []


def test_rewrite_input_asks_llm_for_short_ambiguous_message(patch_llm):
    fake = patch_llm(rewriter.agent, ["我选 D，想做数据分析方向，请据此继续帮我规划"])

    out = run_async(rewrite_input("s1", "D"))

    assert out == "我选 D，想做数据分析方向，请据此继续帮我规划"
    assert len(fake.calls) == 1
    assert "【用户当前输入】\nD" in fake.calls[0]


def test_rewrite_input_falls_back_to_original_on_llm_error(monkeypatch):
    class ExplodingLLM:
        async def ainvoke(self, prompt):
            raise RuntimeError("模拟网络异常")

    monkeypatch.setattr(rewriter.agent, "get_deepseek_llm", lambda *a, **k: ExplodingLLM())

    assert run_async(rewrite_input("s1", "D")) == "D"  # 失败必须原样返回


def test_rewrite_input_falls_back_when_llm_returns_blank(patch_llm):
    patch_llm(rewriter.agent, ["   "])

    assert run_async(rewrite_input("s1", "D")) == "D"


def test_rewrite_input_truncates_over_long_rewrite(patch_llm):
    patch_llm(rewriter.agent, ["补" * 800])

    out = run_async(rewrite_input("s1", "D"))

    assert len(out) == 500  # 防止模型把短句补成一整篇


def test_rewrite_input_strips_whitespace_from_llm_output(patch_llm):
    patch_llm(rewriter.agent, ["  补全后的问题  \n"])

    assert run_async(rewrite_input("s1", "D")) == "补全后的问题"
