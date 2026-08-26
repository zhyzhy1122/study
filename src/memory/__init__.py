# -*- coding: utf-8 -*-
"""
src/memory/__init__.py
作用：记忆模块包入口
统一导出长期记忆存储的读写接口，上层（Agent 链/中间件）从这里导入

为什么单独一个包：
  和 src/agents/ 的基础设施分层：agents 管调度和生命周期，memory 管"记什么怎么取"
  checkpointer（短期）已放 src/agents/checkpointer.py，长期记忆这里统一管理
"""