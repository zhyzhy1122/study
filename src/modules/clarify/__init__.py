
# -*- coding: utf-8 -*-
"""
src/modules/clarify/__init__.py
作用：澄清 Agent 包入口
统一导出 build_clarify_tool，供工具注册中心 / supervisor 导入
和 learning_path / code_review / search 的 __init__.py 保持一致
"""

from src.modules.clarify.agent import build_clarify_tool

__all__ = ["build_clarify_tool"]
