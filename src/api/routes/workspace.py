# -*- coding: utf-8 -*-
"""
src/api/routes/workspace.py
工作空间管理路由

用户需求：在聊天输入框左下角选择"当前工作空间"（即导出目标文件夹），
之后模型把"导出学习计划为 word/md"的文件保存到这个工作空间。

本模块提供三个接口：
  1. GET  /api/workspace         返回当前工作空间路径 + 预置候选目录列表
  2. PUT  /api/workspace         保存用户选定的工作空间路径（校验目录存在且可写）
  3. POST /api/workspace/choose  在服务器桌面上弹出系统文件夹选择框，返回选中目录

工作空间路径持久化到 data/workspace.json：
  这样重启服务后仍记得用户上次选的文件夹。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from fastapi import APIRouter

router = APIRouter(prefix="/api/workspace")
# APIRouter：FastAPI 的"路由分组"，这里的 prefix 让组内所有路由都以 /api/workspace 开头。
# 必须给 prefix，否则组内空路径路由（@router.get("")）会因"prefix 和路径同时为空"而报错。
# 最终路径：GET /api/workspace、PUT /api/workspace、POST /api/workspace/choose

# ========== 路径常量 ==========

_BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
# 项目根目录：E:\学习之家
# __file__ = src/api/routes/workspace.py
# .parent 4 次 = 项目根目录

_DEFAULT_EXPORT_DIR = _BASE_DIR / "data" / "exports"
# 当用户未设置工作空间时的默认导出目录

_WORKSPACE_FILE = _BASE_DIR / "data" / "workspace.json"
# 工作空间持久化文件，保存用户上次选的目录路径

_WORKSPACE_FILE.parent.mkdir(parents=True, exist_ok=True)
# 确保 data 目录存在（若还没有就创建）

# ========== 工作空间读写 ==========

# 模块级缓存：当前工作空间路径，None 表示用户还没设置过
_current: Optional[str] = None


def get_workspace() -> str:
    """
    返回当前应使用的工作空间目录（字符串，带结束分隔符则规范化）。

    优先级：
      1. 内存缓存 _current（本次运行已设过）
      2. 持久化文件 data/workspace.json（上次运行设置过）
      3. 都没设置 → 回落默认导出目录 data/exports

    返回值是绝对路径字符串，供导出功能作为保存目录。
    """
    global _current

    if _current:
        return _current
    # 内存里有值，直接返回，避免重复读文件

    # 尝试从持久化文件恢复
    try:
        if _WORKSPACE_FILE.exists():
            data = json.loads(_WORKSPACE_FILE.read_text(encoding="utf-8"))
            path = data.get("path")
            if path and Path(path).is_dir():
                _current = str(path)
                return _current
    except Exception:
        # 文件损坏等异常情况：静默回落默认目录
        pass

    # 没设置 → 默认导出目录，并顺手落盘，保证 get 稳定
    _current = str(_DEFAULT_EXPORT_DIR)
    _save_workspace(_current)
    return _current


def _save_workspace(path: str) -> None:
    """把工作空间路径写入持久化文件。"""
    _WORKSPACE_FILE.write_text(
        json.dumps({"path": path}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _preset_dirs() -> list[dict]:
    """
    返回预置候选目录列表，用于前端下拉选择。

    每个元素：{"label": 显示名, "path": 绝对路径}
    只包含真实存在的目录，避免前端选到不存在的路径。
    """
    candidates: list[dict] = []
    # 1) 项目默认导出目录
    candidates.append({"label": "项目 data/exports", "path": str(_DEFAULT_EXPORT_DIR)})
    # 2) 用户桌面
    try:
        desk = Path.home() / "Desktop"
        if desk.is_dir():
            candidates.append({"label": "桌面", "path": str(desk)})
    except Exception:
        pass
    # 3) 用户文档
    try:
        docs = Path.home() / "Documents"
        if docs.is_dir():
            candidates.append({"label": "文档", "path": str(docs)})
    except Exception:
        pass
    return candidates


# ========== 路由 ==========


@router.get("")
async def get_workspace_api():
    """
    GET /api/workspace
    返回当前工作空间 + 预置目录列表。

    前端打开"工作空间"面板时调用，用于初始化下拉与回显当前路径。
    """
    current = get_workspace()
    return {
        "current": current,
        "default_dir": str(_DEFAULT_EXPORT_DIR),
        "presets": _preset_dirs(),
    }


@router.put("")
async def set_workspace_api(request: dict):
    """
    PUT /api/workspace
    保存用户选定的工作空间路径。

    请求体: {"path": "D:/我的笔记"}
    校验：目录必须真实存在，否则返回错误提示。
    """
    path = (request.get("path") or "").strip()
    if not path:
        return {"error": "路径不能为空"}

    p = Path(path)
    if not p.is_dir():
        return {"error": f"目录不存在或不可访问: {path}"}

    global _current
    _current = str(p)
    _save_workspace(str(p))
    return {"ok": True, "current": str(p)}


@router.post("/choose")
async def choose_workspace_api():
    """
    POST /api/workspace/choose
    在服务器桌面上弹出系统的"选择文件夹"对话框（资源管理器风格），
    返回用户选中的目录；用户取消则返回取消标记。

    实现细节：
      tkinter.filedialog.askdirectory 会阻塞等待用户操作。
      用 asyncio.to_thread 把它放到独立线程跑，避免卡住 FastAPI 的事件循环。
    """
    import asyncio

    def _ask() -> str:
        import tkinter
        from tkinter import filedialog

        root = tkinter.Tk()
        root.withdraw()  # 隐藏空窗口，只保留文件夹选择框
        root.attributes("-topmost", True)  # 让对话框置顶，不容易被埋到后面
        try:
            return filedialog.askdirectory(title="选择导出工作空间文件夹")
        finally:
            root.destroy()

    # to_thread：阻塞型 GUI 调用丢到后台线程，主协程不被卡
    picked = await asyncio.to_thread(_ask)

    if not picked:
        return {"cancelled": True}
    return {"cancelled": False, "path": picked}