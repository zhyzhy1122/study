# -*- coding: utf-8 -*-
"""
src/memory/store.py
作用：长期记忆的 SQLite 存储层（store）

定位：
  - 只负责"怎么存、怎么取"长期记忆，不碰 Agent 执行流程
  - 存的内容：用户偏好、学习进度、关键结论（按用户/主题精确取，非语义检索）

和 checkpointer 的区别：
  checkpointer（短期）→ data/checkpoints.db，存会话状态/暂停点，生命周期短
  本 store（长期）    → data/memory.db，存跨会话用户信息，长期保留
  两者都是 SQLite，但不同文件、不同表，互不干扰

设计：
  - 表结构：按 namespace（如 "user:{user_id}"）+ key（如 "profile"/"progress"）存 JSON
    这样和 LangGraph store 的 namespace 概念保持一致，将来升级不别扭
  - 纯异步（aiosqlite，和你 checkpointer 一致，Agent 链是异步的）
"""

# ========== 导入部分 ==========

from pathlib import Path
# Path：跨平台处理路径

import json
# json：把记忆内容序列化成 JSON 字符串存库（SQLite 存文本，结构化靠 JSON）

import aiosqlite
# aiosqlite：异步 SQLite 驱动（你 checkpointer 已在用）
# 记忆读写也要异步，因为 Agent 链是异步的，不能阻塞

from typing import Any, Dict, Optional
# Any：任意类型（记忆内容）
# Dict：字典类型（一条完整记忆）
# Optional：可选

from contextlib import asynccontextmanager
# asynccontextmanager：自定义 async with 上下文管理器
# 用它封装"连接生命周期"，调用方只需 async with，不用管建连/关连

# ========== 路径常量 ==========

BASE_DIR = Path(__file__).parent.parent.parent
# 项目根目录（E:\学习之家），由 __file__ 往上三级（memory/store.py → src/memory → src → 根）

MEMORY_DIR = BASE_DIR / "data"
# 记忆库目录（data/，和 checkpointer 的 checkpoints.db 同目录）

MEMORY_DB_PATH = MEMORY_DIR / "memory.db"
# 长期记忆库实际文件路径

# ========== 数据库连接管理（async with 用法，和 checkpointer 一致） ==========

@asynccontextmanager
async def get_store_connection():
    """
    获得长期记忆库的异步连接（async with 用法）

    用法:
        async with get_store_connection() as db:
            await db.execute(...)

    为什么做成 asynccontextmanager：
      和 get_checkpointer 的封装风格一致，调用方不用管建连/关连的生命周期
      内部自动建目录、开连接，退出时自动关闭

    返回:
        aiosqlite.Connection：可执行 SQL 的异步连接
    """
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    # 确保 data/ 目录存在（不存在则创建，幂等）

    async with aiosqlite.connect(str(MEMORY_DB_PATH)) as db:
        # aiosqlite.connect()：建立异步 SQLite 连接
        # async with：自动管理连接生命周期（退出时关闭）
        # 传 str 路径（aiosqlite 在某些版本不接受 Path，转 str 保险）

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS memories (
                namespace TEXT NOT NULL,
                key       TEXT NOT NULL,
                value     TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (namespace, key)
            )
            """
        )
        # 建表（IF NOT EXISTS：已存在就不重复建，幂等）
        # 表结构：
        #   namespace：命名空间，如 "user:alice"（区分不同用户/主题）
        #   key：记忆的键，如 "profile"（个人偏好）、"progress"（进度）、"notes"（结论）
        #   value：记忆内容（JSON 字符串）
        #   updated_at：最后更新时间（字符串，iso 格式）
        #   PRIMARY KEY(namespace, key)：同一命名空间下 key 唯一，重复写就覆盖

        await db.commit()
        # 建表语句要 commit 才真正落库

        yield db
        # yield 把连接交给 async with 的使用方
        # 使用方用完，async with 退出时自动关闭连接

# ========== 写记忆 ==========

async def save_memory(namespace: str, key: str, value: Any) -> None:
    """
    保存一条长期记忆（同名 key 会覆盖）

    参数:
        namespace: 命名空间，如 "user:alice"（谁/哪个主题的记忆）
        key:       记忆的键，如 "profile" / "progress" / "notes"
        value:     记忆内容（任意 JSON 可序列化的数据，如 dict 或 list）

    返回:
        None

    使用:
        await save_memory("user:alice", "profile", {"level": "intermediate", "goal": "AI应用"})
    """
    import datetime
    # datetime：拿当前时间戳（local import，避免高层导入时没拉到）

    payload = json.dumps(value, ensure_ascii=False)
    # 把 value 转成 JSON 字符串（ensure_ascii=False 保留中文，方便看库）
    # value 必须是 JSON 可序列化对象（dict/list/基本类型）

    ts = datetime.datetime.now().isoformat()
    # 当前时间，ISO 格式字符串

    async with get_store_connection() as db:
        # async with：自动提供连接
        await db.execute(
            """
            INSERT INTO memories (namespace, key, value, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(namespace, key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """
            # INSERT ... ON CONFLICT DO UPDATE：SQLite 的 UPSERT
            # 如果 (namespace, key) 已存在 → 覆盖 value 和 updated_at
            # 不存在 → 插入新行
            # 参数用 ? 占位，防止 SQL 注入（值从代码外部传入，绝对不能拼字符串）
            ,
            (namespace, key, payload, ts),
        )
        # 执行写入，参数元组按 ? 顺序填入

        await db.commit()
        # 提交事务，确保写入落库

# ========== 读记忆（单条） ==========

async def get_memory(namespace: str, key: str) -> Optional[Any]:
    """
    读取一条长期记忆

    参数:
        namespace: 命名空间，如 "user:alice"
        key:       记忆的键，如 "profile"

    返回:
        之前存的记忆对象（dict/list），如果不存在返回 None
    """
    async with get_store_connection() as db:
        # 异步连接
        cursor = await db.execute(
            "SELECT value FROM memories WHERE namespace = ? AND key = ?",
            # 按 namespace + key 精确查询
            (namespace, key),
            # 参数占位，防注入
        )
        row = await cursor.fetchone()
        # 取一行；没有则返回 None

        if row is None:
            # 没有找到
            return None

        return json.loads(row[0])
        # row[0] 是 value 字符串，json.loads 还原成原始对象

# ========== 读记忆（整个命名空间） ==========

async def get_all_memories(namespace: str) -> Dict[str, Any]:
    """
    读取某个命名空间下的全部记忆（一个 dict）

    参数:
        namespace: 命名空间，如 "user:alice"

    返回:
        Dict：{key: value}，把该命名空间下所有记忆按 key 组装
        没有则返回空 dict
    """
    async with get_store_connection() as db:
        cursor = await db.execute(
            "SELECT key, value FROM memories WHERE namespace = ?",
            # 查某命名空间下所有 key/value
            (namespace,),
        )
        rows = await cursor.fetchall()
        # 取全部行

        return {key: json.loads(value) for key, value in rows}
        # 组装成 {key: 还原后的对象} 字典返回

# ========== 读所有命名空间（跨用户概览） ==========

async def list_namespaces() -> list:
    """
    列出所有命名空间（调试/管理用）

    返回:
        list：所有存在的 namespace 字符串
    """
    async with get_store_connection() as db:
        cursor = await db.execute("SELECT DISTINCT namespace FROM memories")
        rows = await cursor.fetchall()
        return [row[0] for row in rows]

# ========== 删除记忆（可选，用于清理） ==========

async def delete_memory(namespace: str, key: Optional[str] = None) -> None:
    """
    删除记忆（key 为空则删整个命名空间）

    参数:
        namespace: 命名空间
        key:       要删的键；None 表示删该命名空间全部

    返回:
        None
    """
    async with get_store_connection() as db:
        if key is None:
            # 删整个命名空间
            await db.execute("DELETE FROM memories WHERE namespace = ?", (namespace,))
        else:
            # 删单条
            await db.execute(
                "DELETE FROM memories WHERE namespace = ? AND key = ?",
                (namespace, key),
            )
        await db.commit()
        # 提交删除

# ========== 文件结束 ==========