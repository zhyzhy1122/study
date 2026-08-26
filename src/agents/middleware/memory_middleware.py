# -*- coding: utf-8 -*-
"""
src/agents/middleware/memory_middleware.py
作用：长期记忆中间件（MemoryMiddleware）
    在 Agent 执行前，从 store 读取用户历史记忆，放入 before_ctx，
    供 BaseAgent 在调用 _arun 前拼接进 user_input，让 Agent 记得用户是谁。

挂钩点：before_agent_async（调 Agent 前）
  这个钩子是 Agent 执行前最后一道，正好在 _arun 之前，
  读到的记忆通过 before_ctx 传下去，由 BaseAgent 统一拼接。

和 store 的关系：
  依赖 src/memory/store.py 的 get_all_memories（读取某命名空间全部记忆）
  命名空间：user:{user_id}，user_id 先写死为 LOCAL_USER_ID

设计：
  - 只负责"读内存放 before_ctx"，不负责拼接（拼接在 BaseAgent 模板里做）
  - 这样职责单一，中间件只管"取记忆"，拼接逻辑集中一处，子 Agent 无感知
"""

# ========== 导入部分 ==========

from typing import Any, Dict, Optional
# Any/Dict/Optional：类型标注

from src.agents.base import BaseMiddleware
# 中间件基类（4 个钩子点），本中间件继承它、只重写 before_agent_async

from src.memory.store import get_all_memories
# 长期记忆存储层的读取函数：取某命名空间全部记忆 {key: value}


# ========== 配置：默认用户 ==========
# 目前单机单用户，先写死一个默认用户 ID
# 将来接入 FastAPI 登录/多用户时，改成从调用链传入

LOCAL_USER_ID = "local_user"
# 本地默认用户名
# 记忆命名空间统一用 f"user:{LOCAL_USER_ID}"，即 "user:local_user"


# 基类参数名叫 result，我们内部叫 response，统一一下
# ========== 记忆中间件 ==========

class MemoryMiddleware(BaseMiddleware):
    """
    长期记忆中间件
    在每个 Agent 执行前，读取该用户的长期记忆，放进 before_ctx

    用法：
      在 setup_agent_middlewares 里 add(MemoryMiddleware()) 即可
      BaseAgent.arun() 会读取 before_ctx["memory_context"] 拼进 user_input
    """

    def __init__(self, user_id: str = LOCAL_USER_ID):
        """
        构造函数

        参数:
            user_id: 用户 ID，默认用 LOCAL_USER_ID
        """
        super().__init__(name="memory")
        # 中间件名叫 "memory"，用于日志/调试标识
        # 调用父类构造，把 name 记下来

        self.user_id = user_id
        # 记录用户 ID，读取记忆时用它定位命名空间
        # 实例化时可不传，默认 local_user

    async def before_agent_async(
        self,
        agent_name: str,
        user_input: str,
        **kwargs,
    ) -> Optional[Dict[str, Any]]:
        """
        【异步】Agent 执行前调用：读取长期记忆放入 before_ctx

        参数:
            agent_name: Agent 名称（未用到，但钩子签名要求保留）
            user_input: 用户输入（未直接改，记忆走 before_ctx 传递）
            **kwargs:   额外参数

        返回:
            可选字典，包含:
                memory_context: str，格式化后的历史记忆文本（Agent 可见）
                找不到记忆时 memory_context 为空字符串

        说明：
          中间件出错不应影响 Agent 主流程（和 BaseAgent 里 run_before 的 try/except 一致）
          所以这里读取失败时，返回空的 memory_context 而不抛异常
        """
        try:
            # 读取该用户的全部长期记忆
            namespace = f"user:{self.user_id}"
            # 命名空间：user:local_user
            # 和 store 层约定一致，记忆按用户隔离

            memories = await get_all_memories(namespace)
            # 返回 {key: value} dict，如 {"profile": {...}, "progress": {...}}
            # 这是异步读取（store 是 aiosqlite）

        except Exception as e:
            # 读取失败（库不存在/损坏等），不让主流程崩
            print(f"[记忆中间件] 读取记忆失败: {e}")
            memories = {}
            # 降级为空字典，当作"没有记忆"处理

        if not memories:
            # 该用户还没有任何记忆
            return {"memory_context": ""}
            # 返回空字符串，BaseAgent 拼接时自动跳过记忆段

        # 把记忆结构化成一段清晰的文本，供 Agent 理解
        lines = ["【以下是这个用户的历史记忆，请结合它们来回答】"]
        # 开头提示，告诉 Agent 下面是他之前的信息

        for key, value in memories.items():
            # 遍历每条记忆
            lines.append(f"- {key}: {value}")
            # 每行 "key: value"，如 "- profile: {'level': 'intermediate', ...}"
            # 用 repr 的效果，把 dict 内容直接展示给模型

        memory_text = "\n".join(lines)
        # 多行拼成一段文本

        return {"memory_context": memory_text}
        # 把记忆文本放进 before_ctx
        # BaseAgent.arun() 会从这里取出来拼进 user_input

    async def after_agent_async(
            self,
            agent_name: str,
            result: str,
            **kwargs,
    ) -> Optional[Dict[str, Any]]:
        """
        【异步】Agent 执行后调用：从回答中提炼新信息，写回长期记忆

        参数:
            agent_name: Agent 名称
            result:     Agent 的回答结果（字符串）
            **kwargs:   额外参数，里面包含：
                          - user_input: 用户原始输入
                          - before_ctx: before 阶段的上下文（含旧记忆）

        返回:
            可选字典（after 阶段用，这里主要是副作用——写记忆，返回空也没关系）

        策略:
          1. 不是每次都写——调用 LLM 判断"这次对话有没有值得长期记住的新信息"
          2. 让 LLM 输出结构化 JSON（profile / progress / notes 三类）
          3. 只有"有新增/更新"才写库，避免把记忆库写脏
          4. 出错不影响主流程（try/except 包住）
        """
        # 从 kwargs 里取额外参数（基类通过 kwargs 传进来）
        user_input = kwargs.get("user_input", "")
        # 用户原始输入

        response = result

        before_ctx = kwargs.get("before_ctx", {})
        # before 阶段的上下文（里面有旧记忆，用来对比）
        try:
            # ---- 0. 拿旧记忆（对比用，避免重复存已有的） ----
            namespace = f"user:{self.user_id}"
            old_memories = await get_all_memories(namespace)
            # 旧记忆 dict，如 {"profile": {...}, "progress": {...}}

            # ---- 1. 让 LLM 提炼"有什么值得记住的新信息" ----
            from src.utils.llm import get_deepseek_llm
            # 延迟导入，避免循环

            llm = get_deepseek_llm(temperature=0.1)
            # 低温度，保证输出稳定、结构化

            # 构造提炼提示词
            old_mem_str = str(old_memories) if old_memories else "（空，新用户）"

            extract_prompt = f"""你是"长期记忆提炼助手"。
                你的任务是：从下面的「用户输入」和「Agent回答」中，
                提取出"值得长期记住的用户信息"，更新到长期记忆里。

                【已有记忆】
                {old_mem_str}

                【用户输入】
                {user_input[:1500]}

                【Agent 回答】
                {response[:2500]}

                【输出格式要求】
                只输出一个 JSON 对象，不要任何额外文字，格式如下：
                {{
                "should_update": true/false,
                "profile": {{"要更新的画像字段": "值"}},
                "progress": {{"要更新的进度字段": "值"}},
                "notes": ["新增的关键结论或重要决定"]
                }}

                规则：
                - should_update：只有当确实有"值得长期记住"的新信息时才为 true
                - 不要重复记忆里已有的信息（和已有记忆对比一下，重复的就别更新了）
                - 寒暄、礼貌语、临时问题（如"今天天气"）不值得记
                - 学习目标、基础水平、时间安排、偏好、重要决定、进度变化，才值得记
                - notes 是数组形式，存重要结论/决定/避坑经验
                - 如果没啥值得记的，should_update 设为 false，其它字段留空
            """

            # 调用 LLM 提炼
            llm_result = await llm.ainvoke(extract_prompt)
            # 异步调用（llm 是 LangChain 的 ChatModel，支持 ainvoke）
            extract_text = llm_result.content if hasattr(llm_result, "content") else str(llm_result)

            # ---- 2. 解析 JSON ----
            import json
            import re

            # 模型可能会包 ```json 代码块，先扒掉
            json_match = re.search(r'\{[\s\S]*\}', extract_text)
            if not json_match:
                # 没找到 JSON，当作不需要更新
                return None

            try:
                update_data = json.loads(json_match.group())
            except json.JSONDecodeError:
                # JSON 解析失败，不写记忆（安全兜底）
                return None

            if not update_data.get("should_update", False):
                # 模型说没啥值得记的，直接返回
                return None

            # ---- 3. 写回记忆 ----
            from src.memory.store import save_memory

            # 更新 profile（用户画像）
            profile_updates = update_data.get("profile", {})
            if profile_updates and isinstance(profile_updates, dict):
                # 取旧 profile，合并新的（新覆盖旧）
                old_profile = old_memories.get("profile", {})
                old_profile.update(profile_updates)
                await save_memory(namespace, "profile", old_profile)
                # 合并后写回

            # 更新 progress（学习进度）
            progress_updates = update_data.get("progress", {})
            if progress_updates and isinstance(progress_updates, dict):
                old_progress = old_memories.get("progress", {})
                old_progress.update(progress_updates)
                await save_memory(namespace, "progress", old_progress)

            # 更新 notes（关键结论，追加不覆盖）
            new_notes = update_data.get("notes", [])
            if new_notes and isinstance(new_notes, list):
                old_notes = old_memories.get("notes", [])
                # 去重：同样的结论不重复加
                for note in new_notes:
                    if note and note not in old_notes:
                        old_notes.append(note)
                if old_notes:
                    await save_memory(namespace, "notes", old_notes)

            print(f"[记忆中间件] 已更新长期记忆（{len(profile_updates)} 条画像 + {len(progress_updates)} 条进度 + {len(new_notes)} 条笔记）")

        except Exception as e:
            # 写记忆失败不影响主流程，打个日志就好
            print(f"[记忆中间件] 写回记忆失败: {e}")
            import traceback
            traceback.print_exc()

        return None
        # after 钩子返回 None 就行，主要是副作用（写记忆）
# ========== 文件结束 ==========