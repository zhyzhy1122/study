# -*- coding: utf-8 -*-
"""
tests/test_schema.py
Pydantic 数据模型的校验测试（src/schema/eval.py、src/schema/clarify.py）

覆盖重点：
  - 合法数据能构造；非法数据（越界分数、维度数目不对、未知维度、
    非法 Literal 取值、缺必填字段）必须被拒绝
  - 反思评估的"打回规则"（任一维度 <3 或 总分 <20 → 不通过）与格式化输出
  - 澄清卡片的数据模型与文本渲染、可变默认值的安全性
纯内存校验，无 IO、无网络。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.schema.clarify import ClarifyOption, ClarifyRequest
from src.schema.eval import EvalReport, EvalScore

DIMS = ["Accuracy", "Completeness", "Clarity", "Helpfulness", "Safety"]


def report_from(scores, total=None, passed=True, suggestion=""):
    return EvalReport(
        scores=[EvalScore(dimension=d, score=s) for d, s in zip(DIMS, scores)],
        total=sum(scores) if total is None else total,
        passed=passed,
        suggestion=suggestion,
    )


# ===========================================================================
# EvalScore
# ===========================================================================

def test_eval_score_accepts_valid_dimension_and_range():
    score = EvalScore(dimension="Accuracy", score=4, reason="事实准确")

    assert score.dimension == "Accuracy"
    assert score.score == 4
    assert score.reason == "事实准确"


def test_eval_score_reason_defaults_to_empty_string():
    assert EvalScore(dimension="Safety", score=3).reason == ""


@pytest.mark.parametrize("bad_score", [0, 6, -1, 100])
def test_eval_score_rejects_out_of_range_scores(bad_score):
    with pytest.raises(ValidationError):
        EvalScore(dimension="Accuracy", score=bad_score)


def test_eval_score_rejects_unknown_dimension():
    with pytest.raises(ValidationError):
        EvalScore(dimension="Humor", score=5)


def test_eval_score_requires_dimension_and_score():
    with pytest.raises(ValidationError):
        EvalScore(score=5)
    with pytest.raises(ValidationError):
        EvalScore(dimension="Accuracy")


def test_eval_score_rejects_non_integer_score():
    with pytest.raises(ValidationError):
        EvalScore(dimension="Accuracy", score="四分")


# ===========================================================================
# EvalReport
# ===========================================================================

def test_eval_report_accepts_exactly_five_dimensions():
    report = report_from([5, 5, 5, 5, 5])

    assert len(report.scores) == 5
    assert report.total == 25
    assert report.passed is True
    assert report.suggestion == ""


def test_eval_report_rejects_wrong_number_of_dimensions():
    with pytest.raises(ValidationError):
        EvalReport(
            scores=[EvalScore(dimension="Accuracy", score=5)], total=5, passed=True
        )
    with pytest.raises(ValidationError):
        EvalReport(
            scores=[EvalScore(dimension=d, score=5) for d in DIMS] + [
                EvalScore(dimension="Accuracy", score=5)
            ],
            total=25,
            passed=True,
        )


@pytest.mark.parametrize("bad_total", [4, 26, 0, 100])
def test_eval_report_rejects_total_outside_5_to_25(bad_total):
    with pytest.raises(ValidationError):
        report_from([5, 5, 5, 5, 5], total=bad_total)


def test_eval_report_requires_passed_field():
    with pytest.raises(ValidationError):
        EvalReport(scores=[EvalScore(dimension=d, score=5) for d in DIMS], total=25)


# ===========================================================================
# 打回规则 should_pass —— 与设计文档第六条一致
# ===========================================================================

@pytest.mark.parametrize(
    "scores,expected",
    [
        ([5, 5, 5, 5, 5], True),   # 满分，通过
        ([4, 4, 4, 4, 4], True),   # 总分 20，恰好达标
        ([5, 5, 5, 5, 4], True),   # 总分 24
        ([2, 5, 5, 5, 5], False),  # 任一维度 <3 → 打回（即使总分 22）
        ([5, 5, 5, 5, 1], False),  # 安全性 1 分 → 打回
        ([4, 4, 4, 4, 3], False),  # 各维度都 >=3，但总分 19 < 20 → 打回
        ([3, 3, 3, 3, 3], False),  # 总分 15 < 20 → 打回
    ],
)
def test_should_pass_follows_review_rules(scores, expected):
    report = report_from(scores, passed=not expected)

    # should_pass() 用规则重算，忽略构造时填的 passed
    assert report.should_pass() is expected


def test_should_pass_overrides_stale_passed_flag():
    report = report_from([2, 5, 5, 5, 5], passed=True)
    assert report.should_pass() is False

    report = report_from([5, 5, 5, 5, 5], passed=False)
    assert report.should_pass() is True


def test_format_summary_marks_each_dimension():
    report = report_from([5, 2, 4, 4, 4], suggestion="补示例")

    text = report.format_summary()

    assert "总分: 19/25" in text
    assert "[✓] Accuracy: 5/5" in text
    assert "[✗] Completeness: 2/5" in text  # <3 分用叉标记
    assert text.count("\n") == 5  # 1 行总分 + 5 行维度


# ===========================================================================
# ClarifyOption / ClarifyRequest
# ===========================================================================

def test_clarify_option_requires_id_title_desc():
    option = ClarifyOption(id="A", title="应用开发向", desc="快速做出可用的 LLM 产品")

    assert option.points == []  # default_factory，缺省为空列表


def test_clarify_option_points_default_is_not_shared_between_instances():
    a = ClarifyOption(id="A", title="t", desc="d")
    b = ClarifyOption(id="B", title="t", desc="d")

    a.points.append("污染")

    assert b.points == []  # 可变默认值必须是独立的


def test_clarify_option_rejects_missing_required_fields():
    with pytest.raises(ValidationError):
        ClarifyOption(id="A", title="只有标题")


def test_clarify_request_defaults_and_valid_types():
    request = ClarifyRequest(
        question="你想学 AI，选一条路线：",
        options=[
            ClarifyOption(id="A", title="应用开发向", desc="做产品", points=["LangChain"]),
            ClarifyOption(id="B", title="算法研究向", desc="读论文"),
        ],
    )

    assert request.type == "route_options"
    assert request.allow_more is True
    assert request.context is None


def test_clarify_request_rejects_unknown_type():
    with pytest.raises(ValidationError):
        ClarifyRequest(
            type="something_else",
            question="q",
            options=[ClarifyOption(id="A", title="t", desc="d")],
        )


def test_clarify_request_requires_question_and_options():
    with pytest.raises(ValidationError):
        ClarifyRequest(options=[])
    with pytest.raises(ValidationError):
        ClarifyRequest(question="q")


def test_clarify_request_format_for_user_renders_options_and_more_hint():
    request = ClarifyRequest(
        question="你想学 AI，选一条路线：",
        options=[
            ClarifyOption(
                id="A",
                title="应用开发向",
                desc="面向工程",
                points=["LangChain", "RAG"],
            ),
            ClarifyOption(id="B", title="算法研究向", desc="偏理论"),
        ],
        allow_more=True,
    )

    text = request.format_for_user()

    assert text.startswith("你想学 AI，选一条路线：")
    assert "A) 应用开发向" in text
    assert "     - LangChain" in text
    assert "B) 算法研究向" in text
    assert "你也可以回复：更多想法" in text


def test_clarify_request_format_for_user_hides_more_hint_when_disabled():
    request = ClarifyRequest(
        question="q",
        options=[ClarifyOption(id="A", title="t", desc="d")],
        allow_more=False,
    )

    assert "更多想法" not in request.format_for_user()
