
# -*- coding: utf-8 -*-
"""
src/api/routes/chat.py
聊天接口路由

作用：
  提供 /api/chat 接口，接收用户消息，调用总控 Supervisor，返回回答。
  这是前后端交互的核心接口。

接口设计：
  POST /api/chat
  请求体: {"message": "用户输入的问题"}
  响应体: {"reply": "AI 的回答", "agent_used": "用到的子 Agent 名称"}

为什么用 POST 而不是 GET：
  - 消息可能很长，GET 的 URL 有长度限制
  - 后面还要支持图片上传，POST 是自然选择
  - 语义上，聊天是"动作"不是"查询"，用 POST 更符合 REST 规范
"""

# ========== 导入 ==========

from pydantic import BaseModel, Field
# BaseModel：请求/响应体的 schema 基类
# Field：给字段加描述和校验

from fastapi import APIRouter
# APIRouter：路由路由器，把一组接口挂在一起
# 用 router 而不是直接在 app 上写，方便模块化管理

from src.agents.supervisor import arun_supervisor
# 总控异步入口：传 user_input，返回最终回答
# 这是后端所有 Agent 能力的统一入口
from src.modules.rewriter import rewrite_input
from src.modules.rewriter.agent import async_is_trial_shorthand
# 试探/寒暄短句判断：命中则走"轻量简短回应"，不走完整总控，避免被 kick 成长路线
from src.utils.llm import get_deepseek_llm
# 轻量模型：用于试探短句的简短回复生成（便宜、快）
# 输入重写器入口：把短句补全成完整意思
from src.memory.messages import add_message
# 短期对话历史写入：每轮对话要存进去，重写器才能读到上文
from src.memory.messages import get_recent_messages
# 短期历史读取：自动导出时，若本轮没有生成内容，从这里取最近一条 AI 回答作为导出内容
from src.agents.supervisor import astream_supervisor
# ===== 诊断日志（临时，排查完删掉） =====
import time as _time
import traceback as _traceback
from pathlib import Path as _Path

_LOG_FILE = _Path(__file__).parent.parent.parent.parent / "data" / "debug_chat.log"

def _log(msg: str):
    """写一行诊断日志到文件（带时间戳）"""
    try:
        ts = _time.strftime("%H:%M:%S", _time.localtime())
        with open(str(_LOG_FILE), "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass  # 日志失败不能影响主流程

# ========== 常量 ==========

LOCAL_USER_ID = "local_user"
# 默认用户 ID（单机单用户场景，先写死）
# 保留给旧同步接口 chat() 和长期记忆用；短期对话历史按 session_id 隔离

# ========== 路由实例 ==========

router = APIRouter(prefix="/api", tags=["chat"])
# 创建路由实例

# ========== 健康检查（诊断用） ==========

@router.get("/health")
async def health_check():
    """最简单的健康检查，确认路由能正常响应"""
    _log("[health] 收到健康检查请求")
    return {"status": "ok", "message": "路由正常工作"}
# prefix="/api"：这个路由下的所有接口都以 /api 开头
# tags=["chat"]：在 Swagger 文档里归到 chat 分组，方便查看

# ========== 请求/响应模型 ==========

class ChatRequest(BaseModel):
    """
    聊天请求体 schema

    字段:
        message: str，用户输入的消息（必填）
        image_path: str，可选的本地图片路径（先留着，P4 上传后用）
                    目前阶段前端只发 message，image_path 为空
    """
    message: str = Field(..., min_length=1, max_length=5000,
                         description="用户输入的问题或消息")
    # ... 表示必填
    # min_length=1：不能发空消息
    # max_length=5000：防止超长输入打爆 token

    image_path: str = Field("", description="可选：本地图片文件路径（多模态用）")
    # 目前先留空，等 P4 做图片上传时再用

    session_id: str = Field("", description="会话 ID（对话记录分组用，空则自动生成）")

class ChatResponse(BaseModel):
    """
    聊天响应体 schema

    字段:
        reply: str，AI 的回答内容
        status: str，状态（"success" / "error"）
        error: str | None，错误信息（成功时为 null）
    """
    reply: str = Field("", description="AI 的回答内容")
    status: str = Field("success", description="状态：success / error")
    error: str | None = Field(None, description="错误信息，成功时为 null")

# ========== 接口实现 ==========

@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    【异步】聊天接口

    接收用户消息，调用总控 Supervisor，返回回答。

    参数:
        request: ChatRequest，请求体（FastAPI 会自动解析 JSON 并校验）

    返回:
        ChatResponse，响应体

    流程:
        1. 接收 message 和可选 image_path
        2. 调用 arun_supervisor（总控 → 子 Agent → 工具 → 返回结果）
        3. 包装成 ChatResponse 返回
        4. 出错时返回 status=error，不抛异常给前端
    """
    try:
        _log(f"=== 新请求开始: '{request.message[:30]}' ===")

        # ---- 会话 ID ----
        sid = request.session_id or uuid.uuid4().hex[:12]

        # ---- 构造用户输入 ----
        user_input = request.message
        # 纯文本部分
        _log(f"[1/6] user_input 构造完成: {len(user_input)} 字")

        if request.image_path:
            # 如果有图片路径，把它拼进用户输入里
            # 总控看到"图片路径"会调 multimodal_expert
            # 这是和 P4 多模态上传对接的方式
            user_input += f"\n\n（附图片路径：{request.image_path}）"

        # ---- ① 存用户消息到短期历史（按会话隔离） ----
        _log("[2/6] 开始 add_message(user) ...")
        try:
            await add_message(sid, "user", user_input)
            _log("[2/6] add_message(user) 完成")
        except Exception as e:
            # 存消息失败不影响主流程，打日志继续
            _log(f"[2/6] ❌ add_message(user) 失败: {e}")
            _log(_traceback.format_exc())

        # ---- ② 过重写器补全（按会话取历史） ----
        _log("[3/6] 开始 rewrite_input ...")
        rewritten_input = await rewrite_input(sid, user_input)
        _log(f"[3/6] rewrite_input 完成，结果: '{rewritten_input[:50]}...'")
        # rewrite_input 内部已有异常兜底（失败返回原输入），外层不用再包

        # ---- ③ 调总控（用补全后的输入） ----
        _log("[4/6] 开始 arun_supervisor ...")
        answer = await arun_supervisor(rewritten_input)
        _log("[4/6] arun_supervisor 返回")
        # 走完整的 Agent 链路：总控 → 路由到子 Agent → 工具调用 → 反思 → 返回
        # 这一步是整个后端的核心，所有智能都在这里面
        answer = str(answer or "")
        _log(f"[4/6] answer 转字符串完成，共 {len(answer)} 字")

        # ---- ④ 存AI回答到短期历史（按会话隔离） ----
        _log("[5/6] 开始 add_message(ai) ...")
        try:
            await add_message(sid, "ai", answer)
            _log("[5/6] add_message(ai) 完成")
        except Exception as e:
            # 存消息失败不影响主流程，打日志继续
            _log(f"[5/6] ❌ add_message(ai) 失败: {e}")
            _log(_traceback.format_exc())

        _log("[6/6] 准备返回响应")

        # ---- 空回答兜底：避免前端静默空白 ----
        if not answer.strip():
            return ChatResponse(
                reply="",
                status="error",
                error="总控没有生成有效回答，请换一种说法重试",
            )

        # ---- 返回结果 ----
        return ChatResponse(
            reply=answer,
            status="success",
            error=None,
        )

    except Exception as e:
        # 出错时返回友好的错误信息，不抛 500
        # 前端可以根据 status 判断成功还是失败
        import traceback
        traceback.print_exc()
        # 控制台打完整堆栈，方便调试
        _log(f"❌ chat() 异常: {e}")
        _log(traceback.format_exc())

        return ChatResponse(
            reply="",
            status="error",
            error=f"处理失败：{str(e)}",
        )


from fastapi.responses import StreamingResponse
# StreamingResponse：FastAPI 的流式响应
# 用来返回 SSE 格式的文本流

import json


# json：用来序列化事件数据

@router.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    """
    流式对话接口（SSE 协议）——带思考过程

    事件类型：
        - thinking:    阶段切换/思考中
        - tool_start:  工具开始调用
        - tool_end:    工具调用完成
        - token:       回答逐字输出
        - done:        全部完成
        - error:       出错
    """

    async def event_generator():
        """异步生成器：产出 SSE 事件字符串"""
        import json
        try:
            # ---- 会话 ID ----
            sid = request.session_id or uuid.uuid4().hex[:12]
            title = request.message[:30]

            # ---- 构造用户输入 ----
            user_input = request.message
            if request.image_path:
                user_input += f"\n\n（附图片路径：{request.image_path}）"

            # ---- ① 存用户消息到短期历史（按会话隔离） ----
            try:
                await add_message(sid, "user", user_input)
            except Exception:
                pass

            # ---- ①b 存到对话记录 ----
            try:
                await _init_conv_db()
                await _ensure_session(sid, title)
                await _save_conv_message(sid, "user", request.message)
            except Exception:
                pass

            # ---- ② 过重写器补全（按会话取历史） ----
            rewritten_input = await rewrite_input(sid, user_input)

            # ---- ②b 试探/寒暄短句：不走完整总控，轻量简短回应 ----
            # 触发条件：命中试探/寒暄词表（你好、AI、在吗…）。重写器已把这些原样放行，
            # 但总控 ReAct 仍可能据此调用学习路线专家输出超长内容。这里在入口硬拦截：
            # 只生成一句简短接住+反问，绝不规划、绝不调专家 → 杜绝长路线/重复/内部 JSON。
            if async_is_trial_shorthand(rewritten_input):
                brief = await _brief_reply(rewritten_input)
                full_answer = brief
                import json as _json
                step = 20
                for i in range(0, len(brief), step):
                    data_str = _json.dumps(
                        {"type": "token", "content": brief[i:i + step]},
                        ensure_ascii=False,
                    )
                    yield f"data: {data_str}\n\n"
                # 存 AI 回复到短期历史 + 对话记录（与正常回答保持一致）
                try:
                    await add_message(sid, "ai", brief)
                except Exception:
                    pass
                try:
                    await _save_conv_message(sid, "ai", brief)
                except Exception:
                    pass
                data_str = _json.dumps(
                    {"type": "done", "session_id": sid},
                    ensure_ascii=False,
                )
                yield f"data: {data_str}\n\n"
                return

            # ---- ③ 调总控（流式，边跑边推事件）----
            full_answer = ""

            async for event in astream_supervisor(rewritten_input):
                event_type = event.get("type", "")

                if event_type == "thinking":
                    data_str = json.dumps(
                        {"type": "thinking", "content": event.get("content", "")},
                        ensure_ascii=False,
                    )
                    yield f"data: {data_str}\n\n"

                elif event_type == "tool_start":
                    data_str = json.dumps(
                        {
                            "type": "tool_start",
                            "tool": event.get("tool", ""),
                            "content": event.get("content", ""),
                        },
                        ensure_ascii=False,
                    )
                    yield f"data: {data_str}\n\n"

                elif event_type == "tool_end":
                    data_str = json.dumps(
                        {
                            "type": "tool_end",
                            "tool": event.get("tool", ""),
                            "content": event.get("content", ""),
                        },
                        ensure_ascii=False,
                    )
                    yield f"data: {data_str}\n\n"

                elif event_type == "token":
                    token_text = event.get("content", "")
                    full_answer += token_text
                    data_str = json.dumps(
                        {"type": "token", "content": token_text},
                        ensure_ascii=False,
                    )
                    yield f"data: {data_str}\n\n"

                elif event_type == "done":
                    # ---- ④ 存AI回答到短期历史（按会话隔离） ----
                    try:
                        await add_message(sid, "ai", full_answer)
                    except Exception:
                        pass
                    # ---- ④b 存到对话记录 ----
                    try:
                        await _save_conv_message(sid, "ai", full_answer)
                    except Exception:
                        pass
                    # ---- ④c 用户要求导出 → 自动导出到当前工作空间 ----
                    try:
                        target_fmt = _detect_export_format(request.message)
                        if target_fmt:
                            export_result = await _auto_export(
                                sid, full_answer, target_fmt
                            )
                            if export_result:
                                data_str = json.dumps(
                                    {
                                        "type": "export",
                                        "ok": True,
                                        "path": export_result["path"],
                                        "format": target_fmt,
                                    },
                                    ensure_ascii=False,
                                )
                                yield f"data: {data_str}\n\n"
                    except Exception:
                        pass  # 导出失败不影响回答本身
                    data_str = json.dumps(
                        {"type": "done", "session_id": sid},
                        ensure_ascii=False,
                    )
                    yield f"data: {data_str}\n\n"

        except Exception as e:
            import traceback
            traceback.print_exc()
            error_data = json.dumps(
                {"type": "error", "message": str(e)},
                ensure_ascii=False,
            )
            yield f"data: {error_data}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
        },
    )


# ========== 图片上传（多模态用） ==========

import uuid
from fastapi import UploadFile, File

_UPLOAD_DIR = _Path(__file__).resolve().parent.parent.parent.parent / "data" / "uploads"
_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

@router.post("/upload/image")
async def upload_image(file: UploadFile = File(...)):
    """
    上传图片文件，保存到 data/uploads/ 并返回路径。
    前端拿到 path 后在聊天请求中传 image_path 字段即可触发多模态。
    """
    # 生成唯一文件名，保留原始后缀
    suffix = _Path(file.filename).suffix.lower() if file.filename else ".png"
    if suffix not in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
        suffix = ".png"
    filename = f"{uuid.uuid4().hex[:12]}{suffix}"
    filepath = _UPLOAD_DIR / filename

    content = await file.read()
    filepath.write_bytes(content)

    return {"path": str(filepath), "filename": filename}


# ========== 对话记录管理（conversations.db） ==========

import aiosqlite
import datetime

_CONV_DB_PATH = _Path(__file__).resolve().parent.parent.parent.parent / "data" / "conversations.db"

async def _init_conv_db():
    """初始化对话记录库（幂等）"""
    _CONV_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(_CONV_DB_PATH)) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id  TEXT PRIMARY KEY,
                title       TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS conversation_messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role       TEXT NOT NULL,
                content    TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        await db.commit()

async def _ensure_session(session_id: str, title: str) -> str:
    """确保 session 存在，不存在则创建"""
    now = datetime.datetime.now().isoformat()
    async with aiosqlite.connect(str(_CONV_DB_PATH)) as db:
        cursor = await db.execute(
            "SELECT session_id FROM sessions WHERE session_id = ?", (session_id,)
        )
        row = await cursor.fetchone()
        if not row:
            await db.execute(
                "INSERT INTO sessions (session_id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (session_id, title, now, now),
            )
            await db.commit()
        else:
            await db.execute(
                "UPDATE sessions SET updated_at = ? WHERE session_id = ?",
                (now, session_id),
            )
            await db.commit()
    return session_id

async def _save_conv_message(session_id: str, role: str, content: str):
    """存一条对话记录到 conversations.db"""
    now = datetime.datetime.now().isoformat()
    async with aiosqlite.connect(str(_CONV_DB_PATH)) as db:
        await db.execute(
            "INSERT INTO conversation_messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (session_id, role, content, now),
        )
        await db.execute(
            "UPDATE sessions SET updated_at = ? WHERE session_id = ?",
            (now, session_id),
        )
        await db.commit()


# 启动时初始化
import asyncio
try:
    loop = asyncio.get_running_loop()
    loop.create_task(_init_conv_db())
except RuntimeError:
    pass  # 没有运行中的循环，等第一次请求时再初始化


@router.get("/conversations")
async def list_conversations():
    """获取所有对话列表（按更新时间倒序）"""
    await _init_conv_db()
    async with aiosqlite.connect(str(_CONV_DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT s.session_id, s.title, s.created_at, s.updated_at,
                      (SELECT content FROM conversation_messages
                       WHERE session_id = s.session_id AND role = 'user'
                       ORDER BY id ASC LIMIT 1) as first_message
               FROM sessions s ORDER BY s.updated_at DESC"""
        )
        rows = await cursor.fetchall()
        return [
            {
                "session_id": r["session_id"],
                "title": r["title"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                "first_message": r["first_message"],
            }
            for r in rows
        ]


@router.get("/conversations/{session_id}")
async def get_conversation(session_id: str):
    """获取某个对话的全部消息"""
    await _init_conv_db()
    async with aiosqlite.connect(str(_CONV_DB_PATH)) as db:
        cursor = await db.execute(
            """SELECT role, content, created_at
               FROM conversation_messages
               WHERE session_id = ? ORDER BY id ASC""",
            (session_id,),
        )
        rows = await cursor.fetchall()
        return {
            "session_id": session_id,
            "messages": [
                {"role": r[0], "content": r[1], "created_at": r[2]}
                for r in rows
            ],
        }


@router.delete("/conversations/{session_id}")
async def delete_conversation(session_id: str):
    """删除一个对话"""
    await _init_conv_db()
    async with aiosqlite.connect(str(_CONV_DB_PATH)) as db:
        await db.execute("DELETE FROM conversation_messages WHERE session_id = ?", (session_id,))
        await db.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        await db.commit()
    return {"status": "ok"}


# ========== 导出学习路线 ==========

from src.modules.learning_path.exporter import export_route

# 工作空间（导出目标目录）：
# 优先使用用户前端选定的工作空间；若尚未设置，回落固定默认目录 data/exports
from src.api.routes.workspace import get_workspace

_EXPORT_DIR_DEFAULT = _Path(__file__).resolve().parent.parent.parent.parent / "data" / "exports"
_PATH_EXPORT_DIR_DEFAULT = _Path(_EXPORT_DIR_DEFAULT)
_PATH_EXPORT_DIR_DEFAULT.mkdir(parents=True, exist_ok=True)


def _detect_export_format(text: str):
    """
    检测用户消息是否表达"导出学习计划为 word/md"的意图。

    策略（纯规则，不额外调 LLM，快且省）：
      1. 先排除否定句：任何"不要/不用/别 导出"→ 不触发
      2. 必须含动作词：导出 / 保存成 / 保存为 / 存为
      3. 再看格式词决定返回 docx 还是 md；没指定格式则默认 md

    返回: 'md' 或 'docx'；不触发返回 None
    """
    t = (text or "").lower()
    # 用户明确说"不用导出"，不能误触
    if any(neg in t for neg in ("不要导出", "不用导出", "别导出", "不需要导出")):
        return None
    # 必须含导出类动作词
    if not any(act in t for act in ("导出", "保存成", "保存为", "存为")):
        return None
    # 格式词判定：word/doc/文档 → docx；md/markdown → md
    if any(w in t for w in ("word", "docx", "文档")):
        return "docx"
    if any(w in t for w in ("md", "markdown")):
        return "md"
    # 只说了"导出"没指定格式 → 默认 md（最通用）
    return "md"


async def _brief_reply(short: str) -> str:
    """
    对试探/寒暄短句（你好、AI、在吗…）的轻量简短回复：只自然接住 + 反问需求。

    参数:
        short: 用户/重写后的试探性短句

    返回:
        str：一句简短友好的回复。任何失败都回退到固定模板，绝不抛错。

    为什么不用总控：总控 ReAct 遇到这类词仍可能调用学习路线专家输出超长内容。
    这里用轻量 DeepSeek 直接生成一句克制的话，不接任何工具 → 简短可控。
    """
    fallback = "你是想了解这个方向吗？可以告诉我你的目标（比如想入行、想做项目等），我再帮你具体规划。"
    try:
        llm = get_deepseek_llm(temperature=0.4)
        prompt = (
            "用户只发来一句试探性/寒暄的话。请用一两句自然、亲切的话接住，并反问一句具体需求。\n"
            "【硬性要求】\n"
            "- 绝不给出任何学习路线、技术规划、分阶段列表或资源推荐\n"
            "- 不要使用标题、列表、加粗\n"
            "- 控制在 50 字以内，纯文字，只输出这一句话\n"
            f"用户输入：{short}"
        )
        resp = await llm.ainvoke(prompt)
        text = (resp.content if hasattr(resp, "content") else str(resp)).strip()
        # 清洗：去掉可能的代码围栏与多余换行；控制长度
        text = text.replace("```", "").replace("\n", " ").strip()
        return text if text and len(text) <= 120 else fallback
    except Exception:
        return fallback


async def _auto_export(session_id: str, answer: str, fmt: str):
    """
    自动导出：把内容写入当前工作空间目录，返回结果 dict 或 None。

    参数：
      session_id: 会话 ID（用于取短期历史兜底）
      answer:     本轮 AI 生成内容；若为空，改为取该会话最近一条 AI 消息
      fmt:        目标格式，'md' 或 'docx'

    返回: {"path": 绝对路径}；无可导出内容返回 None
    """
    import asyncio

    content = (answer or "").strip()
    # answer 为空 → 回退：从短期历史取该会话最近一条 AI 回答
    if not content:
        recent = await get_recent_messages(session_id, limit=8)
        for m in reversed(recent):  # reversed：从最近一条往旧找第一条 AI
            if m.get("role") == "ai" and (m.get("content") or "").strip():
                content = m["content"].strip()
                break
    if not content:
        return None  # 没有任何可导出的内容

    fmt = fmt if fmt in ("md", "docx") else "md"
    target_dir = _Path(get_workspace())  # 落点 = 前端选定的工作空间
    target_dir.mkdir(parents=True, exist_ok=True)

    # 文件名带时间戳，避免与历史文件重名覆盖
    import re
    safe_name = f"学习路线_{_time.strftime('%Y%m%d_%H%M%S')}"
    safe_name = re.sub(r'[<>:"/\\|?*]', '', safe_name)
    output_path = str(target_dir / f"{safe_name}.{fmt}")

    # export_route 是同步函数，用 run_in_executor 丢到线程池，避免阻塞事件循环
    loop = asyncio.get_event_loop()
    result_path = await loop.run_in_executor(None, export_route, content, output_path)
    return {"path": result_path}

@router.post("/export")
async def export_learning_path(request: dict):
    """
    导出学习路线到指定格式

    请求体:
    {
        "content": "Markdown 格式的学习路线内容",
        "filename": "Python学习路线",  // 不含后缀
        "format": "md"  // 或 "docx"
    }

    返回:
    {
        "path": "绝对路径",
        "filename": "文件名",
        "format": "md/docx"
    }
    """
    content = request.get("content", "")
    filename = request.get("filename", "学习路线")
    fmt = request.get("format", "md")

    if not content:
        return {"error": "内容为空"}

    if fmt not in ("md", "docx"):
        return {"error": f"不支持的格式: {fmt}，支持 md/docx"}

    # 清理文件名（去掉特殊字符）
    import re
    safe_name = re.sub(r'[<>:"/\\|?*]', '', filename)
    if not safe_name:
        safe_name = "学习路线"

    # 导出目标目录：优先用户选择的工作空间，未设置则回落默认 data/exports
    target_dir = _Path(get_workspace())
    target_dir.mkdir(parents=True, exist_ok=True)
    output_path = str(target_dir / f"{safe_name}.{fmt}")

    try:
        result_path = export_route(content, output_path)
        return {
            "path": result_path,
            "filename": f"{safe_name}.{fmt}",
            "format": fmt,
        }
    except Exception as e:
        return {"error": str(e)}