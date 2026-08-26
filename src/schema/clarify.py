# -*- coding: utf-8 -*-
"""
src/schema/clarify.py
作用：人机交互（需求澄清）环节的数据结构

为什么需要：
  - interrupt 暂停时，要把"选项卡片"的数据抛给外部（前端）
  - 前端需要结构化数据才能渲染成卡片（不然要硬切字符串）
  - 结构化后，前端、后端、interrupt 三方用同一份数据模型

设计原则：
  - 兼容未来前端渲染：id/title/desc/points 足够渲染卡片，可扩展字段
  - 类型安全：用 pydantic 建模，LangGraph 中断也能序列化存储
"""

# ========== 导入部分 ==========

from typing import List, Literal, Optional
# List：列表类型
# Literal：限定取值（只允许用列出的值）
# Optional：可选字段

from pydantic import BaseModel, Field
# BaseModel：pydantic 的基类，用来定义数据模型
# Field：字段级别的约束/默认值/说明

# ========== 单个选项 ==========

class ClarifyOption(BaseModel):
    """
    一个"待用户选择"的选项（对应前端的一张卡片）

    字段说明：
        id:      选项唯一标识（前端点选后回传给后端）
        title:   选项标题（卡片的大标题）
        desc:    选项一句话描述（卡片副标题）
        points:  该路线的 3-5 条要点（卡片里的要点列表）
    """
    id: str = Field(..., description="选项标识，如 'A'、'B'、'C'")
    # id：选项标识
    # 前端点亮卡片后，把这个 id 回传，后端据此知道用户选了哪个
    # ... 表示必填

    title: str = Field(..., description="选项标题，卡片大标题")
    # title：标题，如 "应用开发向"

    desc: str = Field(..., description="选项一句话描述")
    # desc：描述，如 "面向工程，快速做出可用的 LLM 产品"

    points: List[str] = Field(default_factory=list, description="该路线的要点列表")
    # points：3-5 条要点，卡片里展示成要点列表
    # default_factory=list：不传时默认空列表，避免可变默认值的坑

# ========== 整包：人机交互要抛给用户的数据 ==========

class ClarifyRequest(BaseModel):
    """
    一次"需求澄清"请求的完整数据（interrupt 抛给外界的载荷）

    字段说明：
        type:        请求类型，标记这是"路线选择"类澄清
        question:    向用户提出的问题（卡片顶部的引导语）
        options:     待选择的选项列表（2-3 个）
        allow_more:  是否允许用户请求"更多想法"
        context:     附带的上文/说明（可选，辅助理解）
    """
    type: Literal["route_options", "generic"] = "route_options"
    # type：请求类型
    # 目前只有 route_options（路线选择），预留 generic（通用澄清）
    # Literal 限定：要么是 route_options，要么是 generic

    question: str = Field(..., description="向用户提出的问题")
    # question：卡片顶部的引导语，如
    #   "你想学'AI'，但范围较广，请选择一条前进路线："

    options: List[ClarifyOption] = Field(..., description="待选择的选项列表")
    # options：2-3 个 ClarifyOption

    allow_more: bool = True
    # allow_more：是否允许用户点"更多想法"
    # True=显示"更多想法"入口；False=不显示

    context: Optional[str] = None
    # context：附带的上文/说明，可空
    # 例：如"根据你的基础（Python 中级），以下为可选方向"

    def format_for_user(self) -> str:
        """
        把结构化数据格式化成文本（给命令行调试/非前端场景用）

        返回:
            一段易读的文本，含 question + 所有选项
        """
        lines = [self.question]
        # 第一行是问题

        for o in self.options:
            # 遍历每个选项
            lines.append(f"\n{o.id}) {o.title}")
            # 输出 "A) 应用开发向"

            if o.desc:
                # 如果有描述，加带缩进的一行
                lines.append(f"     {o.desc}")
                # 缩进 4 空格：描述

            for pt in o.points:
                # 如果有点，加带缩进的要点行
                lines.append(f"     - {pt}")
                # 每个要点一行，带 "- " 前缀

        if self.allow_more:
            # 如果允许"更多想法"
            lines.append("\n你也可以回复：更多想法")
            # 加一行提示

        return "\n".join(lines)
        # 用换行把所有行连起来返回

# ========== 文件结束 ==========