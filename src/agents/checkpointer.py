# -*- coding: utf-8 -*-
"""
src/agents/checkpointer.py
作用：统一封装会话记忆的 checkpointer（短期记忆存储层）
说明：supervisor 用 ainvoke（异步），所以用 AsyncSqliteSaver
      AsyncSqliteSaver 是异步上下文管理器，用 async with 管理连接生命周期
"""

# ========== 导入部分 ==========

from pathlib import Path
# Path：跨平台处理文件路径

import threading
# threading：用于生成线程安全的自增会话 ID

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
# AsyncSqliteSaver：异步版 SQLite checkpointer
# 总控图用 ainvoke 异步运行，必须用异步版本
# 它支持 interrupt 中断后恢复 + 跨进程记忆持久化
from contextlib import asynccontextmanager          # 新建自定义异步上下文管理器
import aiosqlite                                     # 建异步 SQLite 连接
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

# ========== 路径常量 ==========

BASE_DIR = Path(__file__).parent.parent.parent
# 项目根目录（E:\学习之家），由 __file__ 往上三级得到

CHECKPOINT_DIR = BASE_DIR / "data"
# 数据库文件目录（E:\学习之家\data）

CHECKPOINT_DB_PATH = CHECKPOINT_DIR / "checkpoints.db"
# checkpointer 数据库文件实际路径

# ========== thread_id 生成 ==========

_thread_counter = threading.Lock()
# 线程锁：保证并发下 thread_id 唯一

_last_id = 0
# 上次生成的序号

def gen_thread_id() -> str:
    """生成唯一且递增的会话 ID（thread_id），供 checkpointer 区分会话"""
    # 每次调用返回 thread-1、thread-2、thread-3 ...
    global _last_id
    with _thread_counter:
        _last_id += 1
        return f"thread-{_last_id}"

# ========== 异步 checkpointer 工厂 ==========

async def get_checkpointer():
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def _manager():
        # 显式登记自定义类型，消除警告
        serde = JsonPlusSerializer(
            allowed_msgpack_modules=[
                ("src.agents.base", "ExecutionPlan"),
                ("src.schema.clarify", "ClarifyRequest"),
            ]
        )
        # 建连接时把自定义序列化器挂到 saver 上
        async with aiosqlite.connect(str(CHECKPOINT_DB_PATH)) as conn:
            yield AsyncSqliteSaver(conn, serde=serde)

    return _manager()

# ========== 文件结束 ==========