# -*- coding: utf-8 -*-
"""
tests/test_middleware.py
中间件层测试（logging / memory / reflection + MiddlewareManager 钩子编排）

覆盖重点：
  - MiddlewareManager 的钩子顺序、上下文合并、异常隔离
  - BaseAgent.run/arun 与中间件的协作顺序（before → _run → after）
  - 反思评估中间件的"裁判提示词组装 → JSON 解析 → 打回判定"闭环
  - 记忆中间件的读记忆 / 写回记忆（含合并、去重、should_update 短路）
全部使用假 LLM，不发任何网络请求。
"""

from __future__ import annotations

import json

import pytest

import src.agents.middleware.memory_middleware as memory_mw
import src.agents.middleware.reflection_middleware as reflection_mw
from src.agents.base import BaseAgent, BaseMiddleware, MiddlewareManager
from src.agents.middleware.logging_middleware import LoggingMiddleware
from src.agents.middleware.memory_middleware import MemoryMiddleware
from src.agents.middleware.reflection_middleware import ReflectionMiddleware
from src.memory.store import get_all_memories, get_memory, save_memory
from src.schema.eval import EvalReport, EvalScore

from conftest import run_async


# ===========================================================================
# 辅助件
# ===========================================================================

class RecordingMiddleware(BaseMiddleware):
    """把每次钩子调用记进共享列表，用于断言"谁先谁后"。"""

    def __init__(self, name: str, events: list, box: dict):
        super().__init__(name=name)
        self.events = events
        self.box = box

    def _record(self, hook: str) -> dict:
        self.events.append(f"{self.name}:{hook}")
        return {f"{self.name}_{hook}": True}

    def before_agent(self, agent_name, user_input, **kwargs):
        return self._record("before_agent")

    def before_llm(self, agent_name, prompt, **kwargs):
        return self._record("before_llm")

    def after_llm(self, agent_name, response, **kwargs):
        return self._record("after_llm")

    def after_agent(self, agent_name, result, **kwargs):
        return self._record("after_agent")

    async def before_agent_async(self, agent_name, user_input, **kwargs):
        return self._record("before_agent_async")

    async def after_agent_async(self, agent_name, result, **kwargs):
        return self._record("after_agent_async")


class EchoAgent(BaseAgent):
    """最小可用 Agent：记录收到的输入并回显，用于观察中间件如何影响输入。"""

    def __init__(self, name: str = "echo", events: list | None = None,
                 reply: str = "answer"):
        super().__init__(name=name)
        self.events = events if events is not None else []
        self.reply = reply
        self.seen_inputs: list[str] = []

    def _run(self, user_input: str, before_ctx: dict = None, **kwargs) -> str:
        self.events.append("agent:_run")
        self.seen_inputs.append(user_input)
        return self.reply

    async def _arun(self, user_input: str, before_ctx: dict = None, **kwargs) -> str:
        self.events.append("agent:_arun")
        self.seen_inputs.append(user_input)
        return self.reply


class FailOnceMiddleware(BaseMiddleware):
    """第一次 before_agent 抛异常，之后正常——用于验证异常隔离不会中断链条。"""

    def __init__(self):
        super().__init__(name="fail_once")
        self.calls = 0

    def before_agent(self, agent_name, user_input, **kwargs):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("boom")
        return {"recovered": True}


def make_report(scores, total=None, suggestion="", passed=True) -> EvalReport:
    """按顺序给 5 个维度构造 EvalReport（scores 为 5 个整数）。"""
    dims = ["Accuracy", "Completeness", "Clarity", "Helpfulness", "Safety"]
    return EvalReport(
        scores=[EvalScore(dimension=d, score=s) for d, s in zip(dims, scores)],
        total=sum(scores) if total is None else total,
        passed=passed,
        suggestion=suggestion,
    )


def judge_json(scores, passed=True, suggestion="") -> str:
    """构造一段"裁判模型输出"的 JSON 文本。"""
    dims = ["Accuracy", "Completeness", "Clarity", "Helpfulness", "Safety"]
    return json.dumps(
        {
            "scores": [
                {"dimension": d, "score": s, "reason": f"{d} 还行"}
                for d, s in zip(dims, scores)
            ],
            "total": sum(scores),
            "passed": passed,
            "suggestion": suggestion,
        },
        ensure_ascii=False,
    )


# ===========================================================================
# 1. MiddlewareManager：钩子顺序 / 上下文合并 / 异常隔离
# ===========================================================================

def test_manager_runs_before_hooks_in_registration_order_and_merges_context():
    manager = MiddlewareManager()
    events: list = []
    manager.add(RecordingMiddleware("first", events, {}))
    manager.add(RecordingMiddleware("second", events, {}))

    ctx = manager.run_before_agent("echo", "hi")

    assert events == ["first:before_agent", "second:before_agent"]
    assert ctx == {"first_before_agent": True, "second_before_agent": True}


def test_manager_hook_order_is_identical_for_all_four_hooks():
    """四个钩子点都按"注册顺序"执行（不是洋葱包裹）。"""
    manager = MiddlewareManager()
    events: list = []
    manager.add(RecordingMiddleware("a", events, {}))
    manager.add(RecordingMiddleware("b", events, {}))

    manager.run_before_agent("echo", "hi")
    manager.run_before_llm("echo", "prompt")
    manager.run_after_llm("echo", "response")
    manager.run_after_agent("echo", "result")

    assert events == [
        "a:before_agent", "b:before_agent",
        "a:before_llm", "b:before_llm",
        "a:after_llm", "b:after_llm",
        "a:after_agent", "b:after_agent",
    ]


def test_middleware_exception_is_isolated_and_chain_continues():
    manager = MiddlewareManager()
    faulty = FailOnceMiddleware()
    manager.add(faulty)
    manager.add(FailOnceMiddleware())  # 第二个是全新实例，第一次也炸
    manager.add(LoggingMiddleware())

    # 第一个中间件抛异常 → 被吞掉；链条继续走到后面的中间件
    ctx = manager.run_before_agent("echo", "hi")
    assert "recovered" not in ctx  # 失败那次没有产出上下文
    assert "start_time" in ctx  # 后面的 LoggingMiddleware 仍然执行了

    # 第二次调用：FailOnceMiddleware 恢复正常，异常隔离没有破坏实例状态
    ctx2 = manager.run_before_agent("echo", "hi")
    assert ctx2["recovered"] is True


def test_manager_ignores_non_dict_hook_return_values():
    class JunkMiddleware(BaseMiddleware):
        def __init__(self):
            super().__init__(name="junk")

        def before_agent(self, agent_name, user_input, **kwargs):
            return ["not", "a", "dict"]

    manager = MiddlewareManager()
    manager.add(JunkMiddleware())
    assert manager.run_before_agent("echo", "hi") == {}


def test_async_manager_runs_async_hooks():
    manager = MiddlewareManager()
    events: list = []
    manager.add(RecordingMiddleware("x", events, {}))
    manager.add(RecordingMiddleware("y", events, {}))

    async def scenario():
        before = await manager.run_before_agent_async("echo", "hi")
        after = await manager.run_after_agent_async("echo", "done")
        return before, after

    before, after = run_async(scenario())
    assert events == ["x:before_agent_async", "y:before_agent_async",
                      "x:after_agent_async", "y:after_agent_async"]
    assert before == {"x_before_agent_async": True, "y_before_agent_async": True}
    assert after == {"x_after_agent_async": True, "y_after_agent_async": True}


# ===========================================================================
# 2. LoggingMiddleware
# ===========================================================================

def test_logging_middleware_returns_start_time_and_prints(capsys):
    mw = LoggingMiddleware()
    ctx = mw.before_agent("code_review", "帮我看看这段代码")

    assert isinstance(ctx["start_time"], float)
    out = capsys.readouterr().out
    assert "[Agent 开始] code_review" in out


def test_logging_middleware_prints_placeholder_for_short_input(capsys):
    mw = LoggingMiddleware()
    mw.before_agent("echo", "短输入")

    out = capsys.readouterr().out
    # 输入未超过 100 字 → 不应该出现省略号
    assert "短输入" in out
    assert "..." not in out


def test_logging_middleware_truncates_long_input(capsys):
    mw = LoggingMiddleware()
    long_input = "啊" * 150
    mw.before_agent("echo", long_input)

    out = capsys.readouterr().out
    assert "啊" * 100 in out
    assert "..." in out


def test_logging_middleware_after_agent_reports_duration_from_before_ctx(capsys):
    mw = LoggingMiddleware()
    before_ctx = mw.before_agent("echo", "hi")
    after_ctx = mw.after_agent("echo", "回答内容", before_ctx=before_ctx)

    assert after_ctx["duration_ms"] >= 0
    out = capsys.readouterr().out
    assert "[Agent 结束] echo" in out
    assert "结果长度: 4 字符" in out


def test_logging_middleware_after_agent_without_before_ctx_still_works():
    mw = LoggingMiddleware()
    after_ctx = mw.after_agent("echo", "answer")
    assert "duration_ms" in after_ctx


def test_logging_middleware_async_versions():
    mw = LoggingMiddleware()

    async def scenario():
        before = await mw.before_agent_async("echo", "hi")
        after = await mw.after_agent_async("echo", "answer", before_ctx=before)
        return before, after

    before, after = run_async(scenario())
    assert isinstance(before["start_time"], float)
    assert after["duration_ms"] >= 0


# ===========================================================================
# 3. BaseAgent × MiddlewareManager：钩子与业务逻辑的编排
# ===========================================================================

def test_agent_run_orders_hooks_around_business_logic():
    agent = EchoAgent()
    events = agent.events

    class _Recorder(BaseMiddleware):
        """把 before/after 钩子插进与 _run 相同的事件流，验证时序。"""

        def __init__(self):
            super().__init__(name="order")

        def before_agent(self, agent_name, user_input, **kwargs):
            events.append("mw:before")
            return None

        def after_agent(self, agent_name, result, **kwargs):
            events.append("mw:after")
            return None

    agent.middleware.add(_Recorder())

    result = agent.run("问题")

    assert result == "answer"
    assert events == ["mw:before", "agent:_run", "mw:after"]


def test_agent_run_injects_memory_context_into_user_input():
    agent = EchoAgent()

    class MemoryStub(BaseMiddleware):
        def __init__(self):
            super().__init__(name="memory_stub")

        def before_agent(self, agent_name, user_input, **kwargs):
            return {"memory_context": "【用户画像】零基础"}

    agent.middleware.add(MemoryStub())
    agent.run("我想学 Python")

    assert agent.seen_inputs[0].startswith("我想学 Python")
    assert "【用户画像】零基础" in agent.seen_inputs[0]


def test_agent_run_rewrites_input_when_middleware_returns_new_result():
    agent = EchoAgent()

    class Rewriter(BaseMiddleware):
        def __init__(self):
            super().__init__(name="rewriter")

        def after_agent(self, agent_name, result, **kwargs):
            return {"new_result": "被中间件改写的结果"}

    agent.middleware.add(Rewriter())
    assert agent.run("问题") == "被中间件改写的结果"


# ===========================================================================
# 4. ReflectionMiddleware：裁判提示词 / 解析 / 打回判定
# ===========================================================================

def test_judge_prompt_is_formatted_with_real_inputs():
    mw = ReflectionMiddleware()
    prompt = mw._build_judge_prompt("code_review", "帮我看看这段代码", "代码没问题")

    # .format() 必须真的执行：占位符不能原样留在提示词里
    assert "{user_input}" not in prompt
    assert "{agent_name}" not in prompt
    assert "{result}" not in prompt
    assert "帮我看看这段代码" in prompt
    assert "代码没问题" in prompt
    # 转义后的 JSON 模板应还原成单花括号
    assert '"scores"' in prompt
    assert "{{" not in prompt


def test_extract_json_accepts_inline_dict():
    mw = ReflectionMiddleware()
    assert mw._extract_json('{"total": 20}') == {"total": 20}


def test_extract_json_strips_markdown_code_fence():
    mw = ReflectionMiddleware()
    text = '这是评分：\n```json\n{"total": 22, "passed": true}\n```\n'
    assert mw._extract_json(text) == {"total": 22, "passed": True}


def test_extract_json_returns_empty_dict_on_garbage():
    mw = ReflectionMiddleware()
    assert mw._extract_json("模型跑飞了，没有 JSON") == {}


def test_judge_parses_report_and_recomputes_passed_true(patch_llm):
    # 裁判自己填 passed=false，但 5 个维度都 >=3 且总分 23 → should_pass() 应纠正为 True
    fake = patch_llm(
        reflection_mw,
        [judge_json([5, 5, 4, 5, 4], passed=False, suggestion="可以更详细")],
    )
    mw = ReflectionMiddleware()

    report = mw._judge("learning_path", "给我一份路线", "路线正文")

    assert len(report.scores) == 5
    assert report.total == 23
    assert report.passed is True  # 被 report.should_pass() 重新校准
    assert report.suggestion == "可以更详细"
    # 提示词里应含原始问题与回答，裁判才知道在评什么
    assert "给我一份路线" in fake.calls[0]
    assert "路线正文" in fake.calls[0]


def test_judge_passes_when_calling_the_judge_model_fails(patch_llm):
    class ExplodingLLM:
        def invoke(self, prompt):
            raise RuntimeError("模拟裁判模型调用失败（例如没网/没余额）")

    import src.agents.middleware.reflection_middleware as mod

    original = mod.get_deepseek_llm
    mod.get_deepseek_llm = lambda *a, **k: ExplodingLLM()
    try:
        report = ReflectionMiddleware()._judge("echo", "问题", "回答")
    finally:
        mod.get_deepseek_llm = original

    # 兜底策略：裁判挂了不能误伤正常输出 → 全 5 分 + 通过
    assert report.passed is True
    assert report.total == 25
    assert all(s.score == 5 for s in report.scores)


def test_judge_falls_back_to_safe_pass_when_judge_json_is_invalid(patch_llm):
    # 维度名不在 Literal 白名单里 → pydantic 校验失败 → 兜底"通过"
    bad = json.dumps(
        {
            "scores": [{"dimension": "Vibes", "score": 5}] * 5,
            "total": 25,
            "passed": False,
            "suggestion": "重写",
        },
        ensure_ascii=False,
    )
    patch_llm(reflection_mw, [bad])

    report = ReflectionMiddleware()._judge("echo", "问题", "回答")
    assert report.passed is True
    assert len(report.scores) == 5
    assert report.total == 25


def test_reflection_after_agent_returns_eval_report_in_context():
    class PassEverything(ReflectionMiddleware):
        def _judge(self, agent_name, user_input, result):
            return make_report([5, 5, 5, 5, 5])

    ctx = PassEverything().after_agent("echo", "回答", user_input="问题")
    assert isinstance(ctx["eval_report"], EvalReport)
    assert ctx["eval_report"].passed is True


def test_failing_eval_report_triggers_rewrite_and_retry():
    """不达标 → BaseAgent 带评审建议重跑；这里验证"重跑次数"和"建议确已注入"。"""
    agent = EchoAgent(reply="初稿")
    attempts = {"n": 0}

    class AlwaysFail(BaseMiddleware):
        def __init__(self):
            super().__init__(name="always_fail")

        def after_agent(self, agent_name, result, **kwargs):
            attempts["n"] += 1
            return {
                "eval_report": make_report(
                    [1, 1, 1, 1, 1], suggestion="补上代码示例", passed=False
                )
            }

    agent.middleware.add(AlwaysFail())
    result = agent.run("问题", max_reflect=3)

    assert result == "初稿"  # 3 次重跑后释放最后一次结果
    assert attempts["n"] == 3  # 打回次数不超过 max_reflect
    assert len(agent.seen_inputs) == 4  # 1 次原始 + 3 次重跑
    assert "【评估反馈，请按以下建议改进后重新完整输出】" in agent.seen_inputs[1]
    assert "补上代码示例" in agent.seen_inputs[1]


def test_async_after_agent_uses_thread_offload_and_returns_report(patch_llm):
    patch_llm(reflection_mw, [judge_json([5, 5, 5, 5, 5])])
    mw = ReflectionMiddleware()

    ctx = run_async(mw.after_agent_async("echo", "回答", user_input="问题"))

    assert ctx["eval_report"].passed is True
    assert ctx["eval_report"].total == 25


# ===========================================================================
# 5. MemoryMiddleware：读记忆 → 注入上下文；写回记忆 → 合并 / 去重 / 短路
# ===========================================================================

def test_memory_middleware_returns_empty_context_for_new_user():
    ctx = run_async(MemoryMiddleware(user_id="nobody").before_agent_async("echo", "hi"))
    assert ctx == {"memory_context": ""}


def test_memory_middleware_formats_existing_memories_into_context():
    async def seed():
        await save_memory("user:alice", "profile", {"level": "零基础"})
        await save_memory("user:alice", "progress", {"stage": "阶段一"})

    run_async(seed())

    ctx = run_async(MemoryMiddleware(user_id="alice").before_agent_async("echo", "hi"))

    text = ctx["memory_context"]
    assert "【以下是这个用户的历史记忆，请结合它们来回答】" in text
    assert "- profile:" in text
    assert "零基础" in text
    assert "- progress:" in text
    assert "阶段一" in text


def test_memory_middleware_degrades_gracefully_when_store_read_fails(monkeypatch):
    async def boom(namespace):
        raise RuntimeError("库文件损坏")

    monkeypatch.setattr(memory_mw, "get_all_memories", boom)
    ctx = run_async(MemoryMiddleware(user_id="alice").before_agent_async("echo", "hi"))

    # 读记忆失败不能把主流程带崩，降级成"没有记忆"
    assert ctx == {"memory_context": ""}


def test_memory_middleware_does_not_write_when_should_update_is_false(patch_llm):
    patch_llm(
        None,
        [json.dumps({"should_update": False, "profile": {}, "notes": []}, ensure_ascii=False)],
    )
    mw = MemoryMiddleware(user_id="alice")

    result = run_async(
        mw.after_agent_async("echo", "您好，很高兴见到您", user_input="你好", before_ctx={})
    )

    assert result is None
    assert run_async(get_all_memories("user:alice")) == {}


def test_memory_middleware_merges_profile_and_dedupes_notes(patch_llm):
    async def seed():
        await save_memory("user:alice", "profile", {"level": "零基础", "goal": "转行"})
        await save_memory("user:alice", "notes", ["已确定方向：AI 应用开发"])

    run_async(seed())

    payload = {
        "should_update": True,
        "profile": {"level": "中级", "daily_hours": 2},
        # 第一条与已有笔记重复 → 不应重复写入；第二条是新笔记
        "notes": ["已确定方向：AI 应用开发", "决定先学 Python 语法"],
    }
    fake = patch_llm(None, [json.dumps(payload, ensure_ascii=False)])

    mw = MemoryMiddleware(user_id="alice")
    run_async(
        mw.after_agent_async(
            "echo", "好的，我记下了", user_input="我每天能学两小时", before_ctx={}
        )
    )

    stored = run_async(get_all_memories("user:alice"))
    assert stored["profile"] == {"level": "中级", "goal": "转行", "daily_hours": 2}
    assert stored["notes"] == ["已确定方向：AI 应用开发", "决定先学 Python 语法"]

    # 提炼提示词应带上旧记忆和本轮对话，模型才有对比依据
    prompt = fake.calls[0]
    assert "零基础" in prompt
    assert "我每天能学两小时" in prompt
    assert "好的，我记下了" in prompt


def test_memory_middleware_uses_user_namespace(patch_llm):
    # 先给 bob 存一条旧记忆，这样提炼提示词里会带上"已有记忆"，可断言命名空间隔离
    run_async(save_memory("user:bob", "profile", {"level": "零基础"}))

    fake = patch_llm(
        None,
        [json.dumps({"should_update": True, "profile": {"goal": "学 AI"}}, ensure_ascii=False)],
    )

    run_async(
        MemoryMiddleware(user_id="bob").after_agent_async(
            "echo", "回答", user_input="我想学 AI", before_ctx={}
        )
    )

    # 提炼提示词里应带上 bob 自己的旧记忆（store 内部已按 user:bob 命名空间取数）
    assert "{'level': '零基础'}" in fake.calls[0]
    assert run_async(get_memory("user:bob", "profile")) == {"level": "零基础", "goal": "学 AI"}
    assert run_async(get_all_memories("user:alice")) == {}  # 不串用户


def test_memory_middleware_read_path_is_namespace_scoped():
    run_async(save_memory("user:alice", "profile", {"level": "中级"}))
    run_async(save_memory("user:bob", "profile", {"level": "零基础"}))

    alice_ctx = run_async(MemoryMiddleware(user_id="alice").before_agent_async("echo", "hi"))
    bob_ctx = run_async(MemoryMiddleware(user_id="bob").before_agent_async("echo", "hi"))

    assert "中级" in alice_ctx["memory_context"]
    assert "零基础" not in alice_ctx["memory_context"]
    assert "零基础" in bob_ctx["memory_context"]
    assert "中级" not in bob_ctx["memory_context"]
