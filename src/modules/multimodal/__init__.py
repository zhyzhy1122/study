# -*- coding: utf-8 -*-
"""
src/modules/multimodal/__init__.py
多模态模块包入口
"""

from src.modules.multimodal.agent import MultimodalAgent
from src.modules.multimodal.qwen_vl import call_qwen_vl

__all__ = [
    "MultimodalAgent",
    "call_qwen_vl",
]