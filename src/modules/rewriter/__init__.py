# -*- coding: utf-8 -*-
"""
src/modules/rewriter/__init__.py
输入重写器包入口

统一导出 rewrite_input，供上层（API）使用
"""
from src.modules.rewriter.agent import rewrite_input

__all__ = [
    "rewrite_input",
]