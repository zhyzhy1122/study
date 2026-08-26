# -*- coding: utf-8 -*-
"""
src/schema/eval.py
作用：反思评估闭环的"评分结果"数据结构
     让裁判模型（LLM-as-a-Judge）的输出是严格的、可校验的 JSON，
     而不是一段自由文本，方便程序直接判断"这个回答是否达标"
关联：
    由 src/agents/middleware/reflection_middleware.py 创建和消费
    与 src/schema/clarify.py 同属"结构化交互数据"目录
"""

# ========== 导入部分 ==========

from typing import List, Literal
# List：列表类型，用于 scores 字段（多个维度的打分）
# Literal：限定只能取指定值，用于限制维度名称必须是 5 个之一

from pydantic import BaseModel, Field
# BaseModel：pydantic 的数据模型基类，字段会自动做类型/取值校验
# Field：字段配置器，可以给字段加约束（如范围 1-5）和描述

# ========== 1. 单个维度的打分 ==========

class EvalScore(BaseModel):
    """
    一个维度的打分结果

    属性:
        dimension: 维度名称（只能取 5 个固定值之一）
        score:     该维度得分 1-5
        reason:    裁判给出的打分理由（供人阅读）
    """

    dimension: Literal["Accuracy", "Completeness", "Clarity", "Helpfulness", "Safety"]
    # dimension：打分维度，用 Literal 强制只能从备忘录第六节的 5 个名字里选
    # 这样如果裁判想输出一个不存在的维度名，pydantic 会直接报错，防止脏数据

    score: int = Field(..., ge=1, le=5)
    # score：该维度得分
    # ge=1 表示最小值是 1，le=5 表示最大值是 5
    # "..." 表示这是必填字段；越界（如 0 或 6）会被 pydantic 拒绝
    # 为什么限定 1-5：备忘录第六节规定每个维度 1-5 分

    reason: str = ""
    # reason：打分理由
    # 字符串，默认为空。让裁判说一句"为什么给这个分"，便于人复盘
    # 不是必填（有默认值），所以打分为 5 分满分的维度可以不用写理由

# ========== 2. 一次评估的完整报告 ==========

class EvalReport(BaseModel):
    """
    一次反思评估的完整结果

    属性:
        scores:      五个维度的打分列表（Len 强制为 5）
        total:       总分
        passed:      是否达标（供调度/中间件判断）
        suggestion:  不达标时的改进建议（达标时可为空）

    用途:
        中间件拿到这个对象后，只需看 passed 就知道要不要打回，
        不需要自己再算一遍分
    """

    scores: List[EvalScore] = Field(..., min_length=5, max_length=5)
    # scores：五个维度的打分列表
    # min_length=5 / max_length=5：强制正好 5 个维度，缺一个或多个都报错
    # 为什么：备忘录规定评估就是 5 个维度，数目不对说明裁判输出有问题

    total: int = Field(..., ge=5, le=25)
    # total：总分
    # 五个维度分值相加，范围必然是 5~25（5 个维度 × 每维度 1-5）
    # ge=5 / le=25：校验总分必须落在这个范围内，防止裁判瞎填

    passed: bool
    # passed：是否达标
    # True=通过（可直接采用）；False=打回（需改进或重跑）
    # 判断逻辑（按备忘录第六节）：
    #   任一维度 < 3 分 → 打回
    #   总分 < 20 分     → 打回
    # 这个布尔值由中间件在解析 JSON 后计算好再填进来

    suggestion: str = ""
    # suggestion：不达标时的改进建议
    # 裁判给出"哪里不好、怎么改"，为后续"打回重跑"提供反馈给 Agent
    # 达标时通常为空字符串

    def format_summary(self) -> str:
        """
        把评估报告格式化成一行行的文本，供命令行打印查看

        返回:
            str: 一行一维度的可读文本
        """
        lines = [f"  总分: {self.total}/25"]
        # 先用一行显示总分（满分为 25）
        for s in self.scores:
            mark = "✓" if s.score >= 3 else "✗"
            # 用勾/叉直观标出哪个维度不达标（<3 分）
            lines.append(f"  [{mark}] {s.dimension}: {s.score}/5 - {s.reason}")
            # 逐维度打印：勾叉 + 维度名 + 分数 + 理由
        return "\n".join(lines)
        # 用换行把所有行拼成一整段返回

    def should_pass(self) -> bool:
        """
        用备忘录第六节的"打回规则"重新判断是否达标（供中间件使用）

        返回:
            bool: True=达标；False=需要打回
        """
        if any(s.score < 3 for s in self.scores):
            # 任一维度小于 3 分 → 不达标
            return False
        if self.total < 20:
            # 总分低于 20 分 → 不达标
            return False
        return True
        # 以上两条都不触发 → 达标