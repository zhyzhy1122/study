# -*- coding: utf-8 -*-
"""
tests/test_memory.py
记忆层测试（长期记忆 store.py + 短期对话原文 messages.py）

覆盖重点：
  - 长期记忆的写 / 读 / UPSERT 覆盖 / 命名空间隔离 / 删除 / 中文与嵌套结构往返
  - 全部落盘在 tmp_path（conftest 已把 MEMORY_DB_PATH 重定向），不碰 data/memory.db
  - 短期对话历史的会话隔离、最近 N 条正序返回、计数、清空
纯 aiosqlite，无网络。
"""

from __future__ import annotations

import sqlite3

from src.memory.messages import (
    add_message,
    clear_messages,
    count_messages,
    get_recent_messages,
)
from src.memory.store import (
    delete_memory,
    get_all_memories,
    get_memory,
    get_store_connection,
    list_namespaces,
    save_memory,
)

from conftest import run_async


# ===========================================================================
# 长期记忆 store.py
# ===========================================================================

def test_store_connection_creates_memory_db_with_expected_schema(isolated_project_paths):
    db_path = isolated_project_paths / "memory.db"
    assert not db_path.exists()

    async def touch():
        async with get_store_connection() as db:
            cursor = await db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            return [row[0] for row in await cursor.fetchall()]

    tables = run_async(touch())

    assert "memories" in tables
    assert db_path.exists()  # 建表时确实落在被重定向的临时路径下，而不是 data/


def test_save_then_get_memory_round_trip():
    payload = {"level": "零基础", "goal": "AI 应用开发", "hours": 2, "tags": ["python", "llm"]}

    async def scenario():
        await save_memory("user:roundtrip", "profile", payload)
        return await get_memory("user:roundtrip", "profile")

    assert run_async(scenario()) == payload


def test_get_memory_returns_none_for_missing_key():
    assert run_async(get_memory("user:nobody", "profile")) is None


def test_save_memory_upserts_and_updates_timestamp():
    async def scenario():
        await save_memory("user:upsert", "progress", {"stage": "阶段一"})
        async with get_store_connection() as db:
            cursor = await db.execute(
                "SELECT value, updated_at FROM memories WHERE namespace=? AND key=?",
                ("user:upsert", "progress"),
            )
            first = await cursor.fetchone()

        await save_memory("user:upsert", "progress", {"stage": "阶段二"})

        async with get_store_connection() as db:
            cursor = await db.execute(
                "SELECT value, updated_at FROM memories WHERE namespace=? AND key=?",
                ("user:upsert", "progress"),
            )
            second = await cursor.fetchone()
            cursor = await db.execute(
                "SELECT COUNT(*) FROM memories WHERE namespace=? AND key=?",
                ("user:upsert", "progress"),
            )
            count = (await cursor.fetchone())[0]

        return first, second, count

    first, second, count = run_async(scenario())

    assert count == 1  # UPSERT：同 namespace+key 只有一行
    assert '"阶段二"' in second[0]
    assert first[1] <= second[1]  # updated_at 单调不减（ISO 字符串可比）


def test_get_all_memories_groups_by_key_and_isolates_namespaces():
    async def scenario():
        await save_memory("user:alice", "profile", {"level": "中级"})
        await save_memory("user:alice", "progress", {"stage": "阶段三"})
        await save_memory("user:bob", "profile", {"level": "零基础"})
        return (
            await get_all_memories("user:alice"),
            await get_all_memories("user:bob"),
            await get_all_memories("user:ghost"),
        )

    alice, bob, ghost = run_async(scenario())

    assert alice == {"profile": {"level": "中级"}, "progress": {"stage": "阶段三"}}
    assert bob == {"profile": {"level": "零基础"}}
    assert ghost == {}


def test_list_namespaces_returns_distinct_namespaces():
    async def scenario():
        await save_memory("user:alice", "profile", {"a": 1})
        await save_memory("user:alice", "notes", ["n"])
        await save_memory("user:bob", "profile", {"b": 2})
        return sorted(await list_namespaces())

    assert run_async(scenario()) == ["user:alice", "user:bob"]


def test_delete_memory_single_key_then_whole_namespace():
    async def scenario():
        await save_memory("user:del", "profile", {"a": 1})
        await save_memory("user:del", "notes", ["n"])

        await delete_memory("user:del", "profile")
        after_key_delete = await get_all_memories("user:del")

        await delete_memory("user:del")  # key=None → 删整个命名空间
        after_ns_delete = await get_all_memories("user:del")

        return after_key_delete, after_ns_delete

    after_key_delete, after_ns_delete = run_async(scenario())

    assert after_key_delete == {"notes": ["n"]}
    assert after_ns_delete == {}


def test_memory_round_trip_preserves_chinese_and_nesting(isolated_project_paths):
    """用原生 sqlite3 直查库文件，确认 JSON 以 UTF-8 明文落库（ensure_ascii=False）。"""
    value = {"难点": ["梯度消失", "过拟合"], "备注": {"老师": "李老师"}}

    run_async(save_memory("user:中文", "画像", value))

    db_path = isolated_project_paths / "memory.db"
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT value FROM memories WHERE namespace=? AND key=?", ("user:中文", "画像")
        ).fetchone()

    assert row is not None
    assert "梯度消失" in row[0]  # 明文中文，而不是 \uXXXX 转义

    assert run_async(get_memory("user:中文", "画像")) == value


def test_memory_values_can_be_list_or_scalar():
    async def scenario():
        await save_memory("user:types", "notes", ["a", "b"])
        await save_memory("user:types", "count", 3)
        await save_memory("user:types", "flag", True)
        return await get_all_memories("user:types")

    assert run_async(scenario()) == {"notes": ["a", "b"], "count": 3, "flag": True}


# ===========================================================================
# 短期对话 messages.py
# ===========================================================================

def test_add_message_returns_increasing_rowid(isolated_project_paths):
    async def scenario():
        first = await add_message("s1", "user", "我想学 C++")
        second = await add_message("s1", "ai", "你选 A/B/C？")
        return first, second

    first, second = run_async(scenario())

    assert first >= 1
    assert second == first + 1
    assert (isolated_project_paths / "messages.db").exists()


def test_get_recent_messages_returns_oldest_first():
    async def scenario():
        await add_message("s2", "user", "第一句")
        await add_message("s2", "ai", "第二句")
        await add_message("s2", "user", "第三句")
        return await get_recent_messages("s2")

    rows = run_async(scenario())

    assert [r["content"] for r in rows] == ["第一句", "第二句", "第三句"]
    assert [r["role"] for r in rows] == ["user", "ai", "user"]
    assert all("created_at" in r for r in rows)


def test_get_recent_messages_honours_limit_and_keeps_latest():
    async def scenario():
        for i in range(1, 8):
            await add_message("s3", "user", f"m{i}")
        return await get_recent_messages("s3", limit=3)

    rows = run_async(scenario())

    # 取"最近 3 条"，且按旧→新排列
    assert [r["content"] for r in rows] == ["m5", "m6", "m7"]


def test_messages_are_isolated_per_session():
    async def scenario():
        await add_message("sess-a", "user", "A 的消息")
        await add_message("sess-b", "user", "B 的消息")
        return (
            await get_recent_messages("sess-a"),
            await get_recent_messages("sess-b"),
        )

    a, b = run_async(scenario())

    assert [r["content"] for r in a] == ["A 的消息"]
    assert [r["content"] for r in b] == ["B 的消息"]


def test_count_messages_and_clear_messages():
    async def scenario():
        await add_message("s4", "user", "一")
        await add_message("s4", "ai", "二")
        await add_message("s5", "user", "别的会话")

        before = await count_messages("s4")
        await clear_messages("s4")
        after_clear = await count_messages("s4")
        other_survived = await count_messages("s5")

        await clear_messages("")  # 空 session_id → 清空全部
        all_cleared = await count_messages("s5")

        return before, after_clear, other_survived, all_cleared

    before, after_clear, other_survived, all_cleared = run_async(scenario())

    assert before == 2
    assert after_clear == 0
    assert other_survived == 1  # 清某个会话不影响别的会话
    assert all_cleared == 0


def test_messages_table_has_no_legacy_user_id_column(isolated_project_paths):
    """迁移逻辑应把旧表的 user_id 列删掉，且只保留新的 5 个业务列。"""
    run_async(add_message("s6", "user", "触发建表"))

    with sqlite3.connect(str(isolated_project_paths / "messages.db")) as conn:
        columns = [r[1] for r in conn.execute("PRAGMA table_info(messages)")]

    assert "user_id" not in columns
    assert columns == ["id", "session_id", "role", "content", "created_at"]


def test_messages_migration_drops_legacy_user_id_from_old_table(isolated_project_paths):
    """模拟旧库（带 NOT NULL 的 user_id）后调用新代码，旧列应被自动删除。"""
    db_path = isolated_project_paths / "messages.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO messages (user_id, role, content, created_at) VALUES (?,?,?,?)",
            ("local_user", "user", "旧数据", "2026-01-01T00:00:00"),
        )
        conn.commit()

    # 新代码写入不应再因 user_id NOT NULL 约束而静默失败
    new_id = run_async(add_message("s7", "user", "新数据"))

    assert new_id > 0
    assert run_async(count_messages("s7")) == 1

    with sqlite3.connect(str(db_path)) as conn:
        columns = [r[1] for r in conn.execute("PRAGMA table_info(messages)")]
    assert "user_id" not in columns
