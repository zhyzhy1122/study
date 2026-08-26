# -*- coding: utf-8 -*-
"""
src/modules/rewriter/agent.py
作用：输入重写器 —— 把"简短/歧义/依赖上文的用户输入"补全成完整、自包含的问题

为什么需要它（看你的实际例子）：
  用户先问"想学C++"，AI答"你选 A前端/B后端/C数据分析/D AI方向"，
  用户回一个"D"。
  —— 单独看"D"，谁都看不懂。但配合上文，它是"我选 D，想做 AI 方向"。
  重写器就是干这个：拿历史 + 当前短句 → 补全成完整意思，再喂给总控。

定位：
  - 在"总控接收前"运行（第 3 步接进 API）
  - 只负责"把话补全"，不负责回答用户
  - 用便宜的 DeepSeek，快、省 token（不是主模型那种强生成）

关键决策：
  1. 只有"短输入 + 有历史"才重写（阈值 SHORT_THRESHOLD=15 字）
  2. 重写失败/异常 → 原样返回，绝不让主流程崩
  3. 重写结果带完整语境，总控拿到就能一次看懂
"""

# ========== 导入部分 ==========

from src.utils.llm import get_deepseek_llm
# DeepSeek 模型工厂函数（便宜、快），用来做意图补全

from src.memory.messages import get_recent_messages
# 短期对话历史读取：取最近 N 轮原文，作为重写的"上文"

# ========== 配置常量 ==========

SHORT_THRESHOLD = 15
# 输入长度阈值（字符数）
#   <= 15 → 判定为"可能歧义的短句"，过重写器
#   >  15 → 已经很完整，不过重写器，直接原样返回
# 为什么 15：
#   低于此长度的多半是缩写/指代/选项（D、继续、那下一步呢）
#   到了 15 字以上基本能表达一句完整意思了

HISTORY_LIMIT = 6
# 重写时取最近几轮对话（默认 6 条 = 可覆盖 3 轮 user+ai）
# 够理解"上文说了什么"，又不至于历史太长让 token 爆炸

MEMORY_HELP = True
# 是否把长期记忆也带进重写上下文（增强理解用户：基础/目标等）
# 暂定为 True，让重写器也能参考"用户是零基础/要学C++"这类画像

# 试探性 / 寒暄性短句：命中这些词时不做补全，原样返回。
# 为什么需要：像"你好""在吗""AI"这类词，若交给重写器补全，模型很容易结合历史
# （比如用户正想学 Java）把它扩写成一整段正式提问，进而让总控一口气输出超长内容。
# 这些词本质是试探/打招呼，应交给总控"自然接住"（反问/确认意图），而非 kick 成大型任务。
_SKIP_TRIAL_LOWER = {
    "你好", "您好", "hi", "hello", "嗨", "哈喽",
    "在吗", "喂", "在不在",
    "谢谢", "感谢", "thanks", "thx",
    "嗯", "哦", "啊", "嗯嗯", "ok", "okay", "好", "好的", "行",
    "可以", "试试", "测试", "测试一下",
    "ai", "什么", "啥",
}


def async_is_trial_shorthand(message: str) -> bool:
    """
    判断这条输入是否属于"试探性/寒暄性"短句，应跳过重写。

    参数:
        message: 用户原始输入

    返回:
        bool：True=寒暄/试探词，不重写，交给总控自然接住；
              False=需要 / 可以交给重写器补全
    """
    return message.strip().lower() in _SKIP_TRIAL_LOWER

def async_input_short(message: str) -> bool:
    """
    判断这条输入是否"短到可能需要重写"

    参数:
        message: 用户原始输入

    返回:
        bool：True=短、可能歧义，建议重写；False=已完整，不重写
    """
    return len(message.strip()) <= SHORT_THRESHOLD
    # 去掉首尾空格后数长度（浅显：紧凑的短句才算短）
    # 注意：这是"启发式"。短未必一定歧义，但值得过一遍重写器确认

async def _build_rewrite_prompt(
    session_id: str,
    message: str,
) -> str:
    """
    构造重写用的提示词（含会话历史与当前输入）

    参数:
        session_id: 会话 ID（用于取该会话内的对话历史）
        message:    用户当前这轮输入

    返回:
        str：完整的重写提示词
    """
    # 1. 取最近几轮对话历史（仅当前会话的）
    history = await get_recent_messages(session_id, HISTORY_LIMIT)
    # 返回 [{role, content}, ...]，旧→新

    # 2. 构造"历史"文本
    if history:
        history_lines = []
        for m in history:
            who = "用户" if m["role"] == "user" else "AI"
            history_lines.append(f"{who}: {m['content']}")
        history_text = "\n".join(history_lines)
    else:
        history_text = "（暂无历史，这是第一句）"
    # 转成 "用户: 想学C++ \n AI: 你选A/B/C \n 用户: D" 这样的可读文本

    # 3. 语义要点（可选，把长期记忆也带进来）
    memory_text = ""
    if MEMORY_HELP:
        try:
            
            # 注意：这里如果要带长期记忆，应使用 store.get_all_memories
            # 为简洁，第 2 步先不带长期记忆，仅历史。后续可扩展。
            pass
        except Exception:
            pass
    # 先把长期记忆留空（第 2 步专注历史；第 3 步如需可加）

    # 4. 拼提示词
    prompt = f"""你是"用户意图补全助手"。
你的任务：根据下面给出的「对话历史」和「用户当前输入」，
把"当前输入"补全成一句完整、自包含、不带歧义的话，交给下游 Agent。

【对话历史】
{history_text}

【用户当前输入】
{message}

【要求】
- 结合历史，把当前这句的意思补全。
  用户回了"D"，你要补全成"我选 D，想做这个选项对应的方向，请据此继续帮我规划"之类。
- 如果当前输入已经完整、没有歧义（就是一句完整想问的话），原样照抄即可，不要改动。
- 如果"当前输入"是纯寒暄、打招呼或试探性词（如"你好""在吗""我不确定""AI"等），不要把它扩写成正式提问，直接原样照抄该词即可，交给下游 Agent 自然接住。
- 只输出补全后的那一句话，不要任何前缀、解释、引号。
- 用和当前输入相同的语言回复。
"""
    return prompt

async def _rewrite(session_id: str, message: str) -> str:
    """
    【异步】真正的重写逻辑：构造提示词 → 调 LLM → 返回补全结果

    参数:
        session_id: 会话 ID（用于取该会话内的对话历史）
        message:    用户当前输入

    返回:
        str：补全后的输入（失败时返回原输入）
    """
    try:
        prompt = await _build_rewrite_prompt(session_id, message)
        # 构造带历史的提示词

        llm = get_deepseek_llm(temperature=0.0)
        # 温度 0.0：重写要"确定、不发挥"，不能把用户意思改歪
        # 越接近 0 越保守、越忠实原文

        resp = await llm.ainvoke(prompt)
        # 调 DeepSeek 补全

        rewritten = resp.content if hasattr(resp, "content") else str(resp)
        # 取文本内容

        rewritten = rewritten.strip()
        # 去掉首尾空白

        if not rewritten:
            # 模型返回空 → 兜底用原输入
            return message

        # 防止模型把原问题补过头（比如加了一堆解释），截断到合理长度
        if len(rewritten) > 500:
            rewritten = rewritten[:500]

        return rewritten
        # 返回补全后的输入

    except Exception as e:
        # 调 LLM 失败：绝不让主流程崩，原样返回用户输入
        print(f"[重写器] 补全失败，使用原输入回退: {e}")
        import traceback
        traceback.print_exc()
        return message

async def rewrite_input(session_id: str, message: str) -> str:
    """
    【异步】对外入口：判断是否需要重写，需要则补全，否则原样返回

    参数:
        session_id: 会话 ID（用于取该会话内的对话历史）
        message:    用户当前输入

    返回:
        str：交给下游 Agent 的输入
              - 只有"短输入"才可能被重写（见 SHORT_THRESHOLD）
              - 长输入或重写失败 → 原样返回

    使用:
        rewritten = await rewrite_input(session_id, user_message)
        answer = await arun_supervisor(rewritten)
    """
    # 先判断是否需要重写：不够短 → 直接返回原话
    if not async_input_short(message):
        return message
    # 已经完整、不歧义的长句，直接归总控，省一次调用

    # 是寒暄/试探性短句（你好、在吗、AI…）→ 不重写，交给总控自然接住，
    # 避免被补全成一段超长提问从而触发一次大型任务输出
    if async_is_trial_shorthand(message):
        return message

    # 是短句 → 过重写器补全
    return await _rewrite(session_id, message)