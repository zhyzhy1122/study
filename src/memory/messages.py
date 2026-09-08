# -*- coding: utf-8 -*-
"""
src/memory/messages.py
作用：短期对话历史存储层（原样记录 user/ai 的每一轮对话）

定位：
  只负责"存原样对话、取最近几轮"，不做任何提炼/总结。
  和 store.py（长期记忆，存提炼后的画像）严格分开：
    - store.py     → 存"结论"，如 零基础/学C++/每天1小时，长期保留
    - messages.py  → 存"原文"，如 "D" / "我选D", 短期保留，给输入理解器当上文

为什么需要它：
  输入理解器（重写器）要看懂用户发的一个字母"D"，
  必须知道"上一条你问了 A/B/C"。这份"原文上下文"就存这里。

和 checkpoints.db 的区别：
  checkpoints.db 存的是 LangGraph 图的"执行断点"（interrupt 暂停位置），
  不是聊天记录；本文件存的是"对话原文流水"，两者互不干扰。

设计：
  - 纯异步（aiosqlite），和 store.py / checkpointer 一致
  - messages 表按 user_id 隔离，靠自增 id 排序取最近 N 轮
"""

# ========== 导入部分 ==========

from pathlib import Path
# Path：跨平台处理文件路径

from typing import Optional, List, Dict
# Optional：可选类型
# List：列表类型
# Dict：字典类型

import aiosqlite
# aiosqlite：异步 SQLite 驱动（和 store.py 一致）
# 整个 Agent 链是异步的，记忆读写不能阻塞

from contextlib import asynccontextmanager
# asynccontextmanager：自定义 async with 上下文管理器
# 封装"建连/关连"生命周期，调用方只需 async with

# ========== 路径常量 ==========

BASE_DIR = Path(__file__).parent.parent.parent
# 项目根目录（E:\学习之家）
# messages.py 在 src/memory/ 下，往上三级到根
# 固定注释方式：
#   __file__          = .../src/memory/messages.py
#   .parent           = .../src/memory
#   .parent.parent    = .../src
#   .parent.parent.parent = 项目根

DATA_DIR = BASE_DIR / "data"
# 数据目录（data/，和 memory.db / checkpoints.db 同目录）

MESSAGES_DB_PATH = DATA_DIR / "messages.db"
# 对话历史库文件路径
# 单独一个 messages.db，和 memory.db（长期）、checkpoints.db（执行状态）分开
# 三库隔离，职责清晰，互不干扰、不会串数据

# ========== 数据库连接管理（async with 用法，和 store.py 一致） ==========

@asynccontextmanager
async def get_messages_connection():
    """
    获得对话历史库的异步连接（async with 用法）

    用法:
        async with get_messages_connection() as db:
            await db.execute(...)

    返回:
        aiosqlite.Connection：可执行 SQL 的异步连接
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # 确保 data/ 目录存在（不存在则创建，幂等）

    async with aiosqlite.connect(str(MESSAGES_DB_PATH)) as db:
        # 创建异步 SQLite 连接
        # async with 自动管理连接生命周期（退出时关闭）
        # 转 str 路径（aiosqlite 某些版本不接受 Path）

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL DEFAULT '',
                role       TEXT NOT NULL,
                content    TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        # 建表（IF NOT EXISTS：已存在就不重复建，幂等）
        # 字段说明：
        #   id        自增主键，靠它排序，取"最近 N 轮"就是 ORDER BY id DESC
        #   session_id 会话 ID，不同会话的历史互不干扰
        #   role      'user' 或 'ai'，区分是谁说的
        #   content   原样内容（一句话，一段话都行）
        #   created_at 时间戳（ISO 字符串）

        # 迁移：旧表没有 session_id 列，尝试添加（已有则忽略）
        try:
            await db.execute("ALTER TABLE messages ADD COLUMN session_id TEXT NOT NULL DEFAULT ''")
        except Exception:
            pass  # 列已存在，忽略

        # 迁移：旧表有 NOT NULL 的 user_id 列（旧版按用户隔离用的），
        # 新代码 INSERT 不再写 user_id，会导致所有写入静默失败（NOT NULL 约束），
        # 且 CREATE TABLE IF NOT EXISTS 不会更新已存在的旧表 —— 必须显式删列
        try:
            await db.execute("DROP INDEX IF EXISTS idx_messages_user")
            await db.execute("ALTER TABLE messages DROP COLUMN user_id")
        except Exception:
            pass  # 列不存在（新库），忽略

        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id)"
        )
        # 建索引：加速"按 session_id 取最近若干条"的查询
        # 组合索引(session_id, id) 让"某个会话按 id 排序"走索引，快

        await db.commit()
        # 建表/建索引要 commit 才真正落库

        yield db
        # yield 把连接交给 async with 的使用方

# ========== 写：追加一条消息 ==========

async def add_message(session_id: str, role: str, content: str) -> int:
    """
    追加一条对话记录（按会话隔离）

    参数:
        session_id: 会话 ID（不同会话的历史互不干扰）
        role:      'user'（用户说的）或 'ai'（AI答的）
        content:   原样内容（不转义、不提炼）

    返回:
        int：这条记录的 id（自增序号，方便调试/对账）

    使用:
        await add_message("abc123", "user", "D")
        await add_message("abc123", "ai", "我理解你选D，想做数据分析……")
    """
    import datetime
    # datetime：拿当前时间戳（函数内导入，避免模块顶层过早依赖）

    ts = datetime.datetime.now().isoformat()
    # 当前时间，ISO 格式字符串

    async with get_messages_connection() as db:
        cursor = await db.execute(
            """
            INSERT INTO messages (session_id, role, content, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (session_id, role, content, ts),
        )
        # 参数占位 ?，防止 SQL 注入（content 是用户输入的，必须参数化）
        # 不存 role 之外的任何 agent 内部信息，纯原文

        await db.commit()
        # 提交，落库

        return cursor.lastrowid
        # cursor.lastrowid：刚插入行的自增 id

# ========== 读：取最近 N 条对话 ==========

async def get_recent_messages(
    session_id: str,
    limit: int = 6,
) -> List[Dict[str, object]]:
    """
    取某会话最近 N 条对话（按时间倒序返回，最新的在最后）

    参数:
        session_id: 会话 ID（只返回该会话的消息）
        limit:      取几条（默认 6 轮，够理解上下文又不至于 token 太大）

    返回:
        List[Dict]: [{role:..., content:..., created_at:...}, ...]
                    按时间正序（旧→新），最新的在列表末尾
        没有记录返回空列表

    使用:
        # 取最近 6 条
        history = await get_recent_messages("abc123", 6)
    """
    async with get_messages_connection() as db:
        cursor = await db.execute(
            """
            SELECT role, content, created_at
            FROM messages
            WHERE session_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (session_id, limit),
        )
        # 按 id 倒序取最近 limit 条：ORDER BY id DESC LIMIT ?
        # 这样拿到的是"最新几条"，但顺序是倒的（新旧颠倒）

        rows = await cursor.fetchall()
        # 取全部结果

        # 倒序拿到的，要再反转成"旧→新"（最新的在末尾），
        # 这样重写器读起来才像正常对话顺序
        rows.reverse()
        # 就地反转：倒序 → 正序

        return [
            {"role": role, "content": content, "created_at": created_at}
            for role, content, created_at in rows
        ]
        # 组装成 dict 列表返回

# ========== 统计：某用户共多少条 ==========

async def count_messages(session_id: str) -> int:
    """
    统计某会话的对话总条数

    参数:
        session_id: 会话 ID

    返回:
        int：该会话的对话条数（0 表示还没有消息）
    """
    async with get_messages_connection() as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ?",
            (session_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0
        # row[0] 是 COUNT(*) 的值

# ========== 可选：清空某用户的历史 ==========

async def clear_messages(session_id: str) -> None:
    """
    清空某会话的对话历史（新建对话 / 调试用）

    参数:
        session_id: 会话 ID；None 或 "" 时清空所有消息

    返回:
        None
    """
    async with get_messages_connection() as db:
        if session_id in (None, ""):
            # 清空全部（调试场景）
            await db.execute("DELETE FROM messages")
        else:
            # 只清某个会话
            await db.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        await db.commit()
        # 提交删除

# ========== 文件结束 ==========