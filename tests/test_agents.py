# -*- coding: utf-8 -*-
"""
tests/test_agents.py
Agent 基类 / 注册表 / 总控 Supervisor 的离线测试

覆盖重点：
  - BaseAgent 的模板方法 run/arun：同步与异步同构、_arun 默认走线程池
  - get_agent 懒加载注册表 + 自动挂载默认中间件的顺序
  - setup_agent_middlewares 的挂载顺序（logging → memory → reflection）
  - Agent 工具组：StructuredTool 的 name/description/args_schema/同步异步实现
  - Supervisor：_extract_answer 的多种返回形态、build 时工具接线、单例缓存
所有 LLM 都被替换成假实现（create_agent / get_deepseek_llm 全部打桩）。
"""

from __future__ import annotations

import pytest
from langchain_core.tools import BaseTool

import src.agents.base as base_mod
import src.agents.supervisor as supervisor_mod
import src.modules.tools.agent_tools as agent_tools_mod
from src.agents.base import (
    BaseAgent,
    BaseMiddleware,
    ExecutionPlan,
    PlanStep,
    get_agent,
    setup_agent_middlewares,
)
from src.agents.middleware.logging_middleware import LoggingMiddleware
from src.agents.middleware.memory_middleware import MemoryMiddleware
from src.agents.middleware.reflection_middleware import ReflectionMiddleware
from src.modules.tools.agent_tools import (
    CodeReviewInput,
    LearningPathInput,
    SearchInput,
    build_all_agent_tools,
    build_code_review_tool,
    build_learning_path_tool,
    build_search_tool,
)

from conftest import run_async


def _always_pass_report():
    """构造一份"通过"的评估报告，用来替掉真实裁判模型。"""
    from src.schema.eval import EvalReport, EvalScore

    dims = ["Accuracy", "Completeness", "Clarity", "Helpfulness", "Safety"]
    return EvalReport(
        scores=[EvalScore(dimension=d, score=5) for d in dims],
        total=25,
        passed=True,
    )


# ===========================================================================
# 1. 计划数据结构（总控 Plan-and-Execute 的数据契约）
# ===========================================================================

def test_plan_step_and_execution_plan_accept_valid_data():
    step = PlanStep(agent="learning_path", action="规划", description="生成路线")
    plan = ExecutionPlan(
        intent="learning_path", steps=[step], mode="serial", summary_hint="汇总要点"
    )

    assert plan.intent == "learning_path"
    assert plan.mode == "serial"
    assert plan.steps[0].agent == "learning_path"
    assert plan.summary_hint == "汇总要点"


def test_execution_plan_defaults_mode_to_serial_and_rejects_unknown_intent():
    plan = ExecutionPlan(intent="code_review", steps=[])
    assert plan.mode == "serial"  # 默认串行
    assert plan.summary_hint == ""

    with pytest.raises(Exception):
        # intent 用 Literal 限定，写错的意图必须在构造期就被拒绝
        ExecutionPlan(intent="随便写的意图", steps=[])


def test_plan_step_action_is_optional():
    assert PlanStep(agent="search").action is None


# ===========================================================================
# 2. BaseAgent 模板方法：同步 / 异步同构
# ===========================================================================

class _RecordingAgent(BaseAgent):
    def __init__(self, name="recorder"):
        super().__init__(name=name)
        self.inputs: list[str] = []

    def _run(self, user_input, before_ctx=None, **kwargs):
        self.inputs.append(user_input)
        return f"sync:{user_input}"


class _SyncOnlyAgent(BaseAgent):
    """只实现同步 _run：验证异步入口 _arun 的线程池兜底。"""

    def __init__(self):
        super().__init__(name="sync_only")
        self.thread_ids: list[int] = []

    def _run(self, user_input, before_ctx=None, **kwargs):
        import threading

        self.thread_ids.append(threading.get_ident())
        return f"sync-only:{user_input}"


def test_base_agent_default_run_raises_not_implemented():
    class Bare(BaseAgent):
        pass

    with pytest.raises(NotImplementedError):
        Bare("bare").run("问题")


def test_agent_run_returns_business_result_without_middlewares():
    agent = _RecordingAgent()
    assert agent.run("你好") == "sync:你好"


def test_agent_arun_falls_back_to_thread_for_sync_only_subclass():
    import threading

    agent = _SyncOnlyAgent()
    main_thread = threading.get_ident()

    result = run_async(agent.arun("问题"))

    assert result == "sync-only:问题"
    assert agent.thread_ids[0] != main_thread  # 确实放到了其它线程，没阻塞事件循环


def test_agent_arun_uses_async_hook_before_agent():
    class AsyncMemory(BaseMiddleware):
        def __init__(self):
            super().__init__(name="async_memory")

        async def before_agent_async(self, agent_name, user_input, **kwargs):
            return {"memory_context": "【记忆】用户是零基础"}

    agent = _RecordingAgent()
    agent.middleware.add(AsyncMemory())

    result = run_async(agent.arun("学 Python"))

    assert "【记忆】用户是零基础" in agent.inputs[0]
    assert result.startswith("sync:学 Python")


# ===========================================================================
# 3. 注册表与默认中间件
# ===========================================================================

def test_get_agent_lazily_builds_and_caches_learning_path(monkeypatch):
    import importlib

    calls = {"n": 0}

    class FakeLearningPathAgent(BaseAgent):
        def __init__(self):
            super().__init__(name="learning_path")
            calls["n"] += 1

        def _run(self, user_input, before_ctx=None, **kwargs):
            return "路线"

    lp_module = importlib.import_module("src.modules.learning_path.agent")
    monkeypatch.setattr(lp_module, "LearningPathAgent", FakeLearningPathAgent)

    first = get_agent("learning_path")
    second = get_agent("learning_path")

    assert isinstance(first, FakeLearningPathAgent)
    assert first is second  # 懒加载后进注册表，二次调用复用同一实例
    assert calls["n"] == 1
    # 用模块属性访问：conftest 会把 AGENT_REGISTRY 换成干净的 dict（autouse 隔离）
    assert base_mod.AGENT_REGISTRY["learning_path"] is first


def test_get_agent_raises_on_unknown_name():
    with pytest.raises(ValueError) as err:
        get_agent("not_an_agent")

    assert "未知的 Agent" in str(err.value)


def test_setup_agent_middlewares_mounts_defaults_in_documented_order():
    agent = _RecordingAgent()
    assert agent.middleware.middlewares == []

    setup_agent_middlewares(agent)

    names = [mw.name for mw in agent.middleware.middlewares]
    assert names == ["logging", "memory", "reflection"]
    assert isinstance(agent.middleware.middlewares[0], LoggingMiddleware)
    assert isinstance(agent.middleware.middlewares[1], MemoryMiddleware)
    assert isinstance(agent.middleware.middlewares[2], ReflectionMiddleware)


def test_agent_with_default_middlewares_runs_before_hooks_in_order(capsys):
    """logging 先打开始日志、memory 再读记忆；两者都在 _run 之前完成。"""
    events: list[str] = []

    class OrderProbe(_RecordingAgent):
        def _run(self, user_input, before_ctx=None, **kwargs):
            events.append("agent:_run")
            return super()._run(user_input, before_ctx=before_ctx, **kwargs)

    agent = OrderProbe()
    setup_agent_middlewares(agent)
    # reflection 会真的调裁判模型 → 换成永远通过的假实现
    agent.middleware.middlewares[2]._judge = lambda *a, **k: _always_pass_report()

    result = agent.run("帮我规划路线")
    out = capsys.readouterr().out

    assert result == "sync:帮我规划路线"
    assert events == ["agent:_run"]
    assert "[Agent 开始] recorder" in out
    assert "[Agent 结束] recorder" in out


# ===========================================================================
# 4. 子 Agent 工具（StructuredTool）
# ===========================================================================

def test_learning_path_tool_contract():
    tool = build_learning_path_tool()

    assert isinstance(tool, BaseTool)
    assert tool.name == "learning_path_expert"
    assert "学习路线" in tool.description
    assert tool.args_schema is LearningPathInput
    assert tool.coroutine is not None  # 总控异步链路要用它


def test_code_review_tool_contract_and_argument_defaults():
    tool = build_code_review_tool()
    assert tool.name == "code_review_expert"
    assert tool.args_schema is CodeReviewInput
    # language / extra_requirements 有默认值，只有 code_content 必填
    assert set(CodeReviewInput.model_fields) == {
        "code_content",
        "language",
        "extra_requirements",
    }
    assert CodeReviewInput(code_content="print(1)").language == "python"


def test_search_tool_contract():
    tool = build_search_tool()
    assert tool.name == "search_expert"
    assert tool.args_schema is SearchInput
    assert SearchInput(query="x").max_results == 5


def test_tool_invocation_routes_to_sub_agent(monkeypatch):
    """工具被调用时，应把结构化参数拼成自然语言后交给对应子 Agent。"""
    captured: dict = {}

    class FakeAgent:
        def run(self, user_input, **kwargs):
            captured["input"] = user_input
            return "审查报告"

        async def arun(self, user_input, **kwargs):
            captured["input"] = user_input
            return "审查报告"

    monkeypatch.setattr(agent_tools_mod, "get_agent", lambda name: FakeAgent())

    tool = build_code_review_tool()
    out = tool.invoke(
        {"code_content": "print(1)", "language": "python", "extra_requirements": "看性能"}
    )

    assert out == "审查报告"
    assert "print(1)" in captured["input"]
    assert "python" in captured["input"]
    assert "看性能" in captured["input"]


def test_search_tool_async_invocation_routes_to_arun(monkeypatch):
    captured: dict = {}

    class FakeAgent:
        async def arun(self, user_input, **kwargs):
            captured["input"] = user_input
            return "搜索结果"

    monkeypatch.setattr(agent_tools_mod, "get_agent", lambda name: FakeAgent())
    tool = build_search_tool()

    out = run_async(tool.ainvoke({"query": "PyTorch 2.0", "max_results": 3}))

    assert out == "搜索结果"
    assert "PyTorch 2.0" in captured["input"]
    assert "最多 3 条结果" in captured["input"]


def test_build_all_agent_tools_respects_configured_api_keys(monkeypatch):
    """没配 Key 的功能不注册工具：总控看不见就不会误调。"""
    from src.config import settings

    monkeypatch.setattr(settings, "tavily_api_key", "", raising=False)
    monkeypatch.setattr(settings, "dashscope_api_key", "", raising=False)

    names = [t.name for t in build_all_agent_tools()]
    assert names == ["learning_path_expert", "code_review_expert"]

    monkeypatch.setattr(settings, "tavily_api_key", "tvly-fake", raising=False)
    monkeypatch.setattr(settings, "dashscope_api_key", "dash-fake", raising=False)

    names = [t.name for t in build_all_agent_tools()]
    assert names == [
        "learning_path_expert",
        "code_review_expert",
        "search_expert",
        "multimodal_expert",
    ]


# ===========================================================================
# 5. Supervisor：结果提取 / 图构建接线 / 单例缓存
# ===========================================================================

class _Msg:
    def __init__(self, content):
        self.content = content


def test_extract_answer_from_ai_message():
    result = {"messages": [_Msg("第一条"), _Msg("最终回答")]}
    assert supervisor_mod._extract_answer(result) == "最终回答"


def test_extract_answer_from_plain_dict_message():
    result = {"messages": [{"role": "user", "content": "问题"}, {"content": "字典形态答案"}]}
    assert supervisor_mod._extract_answer(result) == "字典形态答案"


def test_extract_answer_joins_text_content_blocks():
    """create_agent 的 AIMessage.content 可能是 content block 列表。"""
    result = {
        "messages": [
            _Msg([{"type": "text", "text": "第一段"}, {"type": "tool_use", "id": "x"},
                  {"type": "text", "text": "第二段"}])
        ]
    }
    assert supervisor_mod._extract_answer(result) == "第一段\n第二段"


def test_extract_answer_handles_empty_and_none_content():
    assert supervisor_mod._extract_answer({}) == ""
    assert supervisor_mod._extract_answer({"messages": []}) == ""
    assert supervisor_mod._extract_answer({"messages": [_Msg(None)]}) == ""


def test_build_plain_supervisor_wires_registry_tools_and_caches(monkeypatch):
    from src.modules.tools.registry import get_tool_registry

    built: dict = {}
    sentinel_graph = object()

    def fake_create_agent(model=None, tools=None, system_prompt=None, **kwargs):
        built["tools"] = tools
        built["system_prompt"] = system_prompt
        built["model"] = model
        return sentinel_graph

    monkeypatch.setattr(supervisor_mod, "create_agent", fake_create_agent)
    monkeypatch.setattr(supervisor_mod, "get_deepseek_llm", lambda *a, **k: "fake-llm")
    monkeypatch.setattr(supervisor_mod, "_supervisor_agent", None, raising=False)

    first = supervisor_mod.build_supervisor_agent()
    second = supervisor_mod.build_supervisor_agent()

    assert first is sentinel_graph
    assert first is second  # 单例缓存
    assert built["model"] == "fake-llm"
    assert "总控调度专家" in built["system_prompt"]

    tool_names = [t.name for t in built["tools"]]
    assert "learning_path_expert" in tool_names
    assert "code_review_expert" in tool_names
    # 工具确实来自全局注册中心，而不是临时列表
    assert built["tools"] == get_tool_registry().get_all_tools()


def test_build_supervisor_with_checkpointer_is_not_cached(monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        supervisor_mod,
        "create_agent",
        lambda **kwargs: calls.append(kwargs) or f"graph-{len(calls)}",
    )
    monkeypatch.setattr(supervisor_mod, "get_deepseek_llm", lambda *a, **k: "fake-llm")

    g1 = supervisor_mod.build_supervisor_agent(checkpointer="saver-1")
    g2 = supervisor_mod.build_supervisor_agent(checkpointer="saver-2")

    assert (g1, g2) == ("graph-1", "graph-2")  # 每次新建，不共享 checkpointer
    assert calls[0]["checkpointer"] == "saver-1"
    assert calls[1]["checkpointer"] == "saver-2"


def test_run_supervisor_invokes_agent_with_messages_and_extracts_answer(monkeypatch):
    seen: dict = {}

    class FakeGraph:
        def invoke(self, payload, **kwargs):
            seen["payload"] = payload
            return {"messages": [_Msg("总控的最终回答")]}

    monkeypatch.setattr(supervisor_mod, "build_supervisor_agent", lambda *a, **k: FakeGraph())

    assert supervisor_mod.run_supervisor("帮我规划 AI 学习路线") == "总控的最终回答"
    assert seen["payload"] == {"messages": [{"role": "user", "content": "帮我规划 AI 学习路线"}]}


def test_arun_supervisor_without_thread_id_uses_plain_agent(monkeypatch):
    seen: dict = {}

    class FakeGraph:
        async def ainvoke(self, payload, **kwargs):
            seen["payload"] = payload
            return {"messages": [_Msg("异步回答")]}

    monkeypatch.setattr(supervisor_mod, "build_supervisor_agent", lambda *a, **k: FakeGraph())

    out = run_async(supervisor_mod.arun_supervisor("问题"))

    assert out == "异步回答"
    assert seen["payload"]["messages"][0]["content"] == "问题"


def test_arun_supervisor_with_thread_id_passes_config_and_checkpointer(monkeypatch):
    from contextlib import asynccontextmanager

    seen: dict = {}

    class FakeGraph:
        async def ainvoke(self, payload, config=None, **kwargs):
            seen["config"] = config
            return {"messages": [_Msg("带会话的回答")]}

    @asynccontextmanager
    async def fake_checkpointer():
        yield "fake-saver"

    def _fake_builder(saver):
        seen["saver"] = saver
        return FakeGraph()

    monkeypatch.setattr(
        supervisor_mod, "_build_supervisor_with", _fake_builder
    )

    import src.agents.checkpointer as checkpointer_mod

    monkeypatch.setattr(checkpointer_mod, "get_checkpointer", fake_checkpointer)

    out = run_async(supervisor_mod.arun_supervisor("问题", thread_id="thread-9"))

    assert out == "带会话的回答"
    assert seen["saver"] == "fake-saver"
    assert seen["config"] == {"configurable": {"thread_id": "thread-9"}}
