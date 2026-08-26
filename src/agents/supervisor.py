# -*- coding: utf-8 -*-
"""
src/agents/supervisor.py
作用：总控 Agent（Supervisor）—— 用 create_agent 构建的 ReAct 总调度

改造说明（路径 B）：
  旧版：手写 StateGraph + planner/scheduler/summarizer 三个节点
  新版：create_agent 生成的 ReAct Agent，持有全部工具
        工具包括：
          1. 子 Agent 工具（learning_path_expert / code_review_expert / search_expert）
          2. 未来：MCP 工具（tavily / filesystem / github ...）

架构：
  用户输入 → Supervisor Agent（create_agent）
                ├── 思考：用户要什么？
                ├── 决策：派哪个专家 / 调哪个工具？
                ├── 执行：调用工具/子 Agent
                └── 汇总：把结果整理给用户

为什么用 create_agent 做总控：
  - 符合你"用 create_agent 做"的明确要求
  - 总控本身就是一个带工具的 Agent，能自主决策、灵活调度
  - 工具统一从 ToolRegistry 拿，扩展性强
  - 子 Agent 内部仍然独立完整（有中间件、有反思评估），只是对总控接口是工具形态

注意：
  本项目使用的 create_agent 签名为 create_agent(model, tools, system_prompt)
  输入格式为 {"messages": [...]}，输出为 {"messages": [...]}（最后一条为最终回答）
"""

# ========== 导入部分 ==========

from typing import Optional
# Optional：可选类型（thread_id 等可空字段）

from langchain.agents import create_agent
# create_agent：LangChain v1 的 Agent 构建函数
# 本项目中签名：create_agent(model, tools, system_prompt)
# 返回一个编译好的 Agent graph（StateGraph）
# 输入格式：{"messages": [...]}
# 输出格式：{"messages": [...]}，最后一条消息是最终回答

from src.config import settings
# 导入全局配置（app_name 等）

from src.utils.llm import get_deepseek_llm
# 导入 DeepSeek 模型工厂函数

from src.modules.tools import get_tool_registry, init_tools
# 从工具注册中心导入：
#   get_tool_registry：拿注册中心单例
#   init_tools：初始化工具（注册所有默认工具组）


# ========== 总控系统提示词 ==========

SUPERVISOR_SYSTEM_PROMPT = f"""你是 {settings.app_name} 的总控调度专家（Supervisor）。
你的职责是理解用户的需求，决定如何处理，并调用合适的工具或专家来完成任务。

## 你可以调用的专家（子 Agent 工具）

1. **learning_path_expert**（学习路线规划专家）
     - 用途：当用户明确要求规划学习路线、详细学习计划、分阶段学习方案时使用
   - 注意：如果用户只是简单提到"想学XX"、"XX难不难"等试探性问题，不要直接调用路线专家
     你自己先简短回答 + 反问用户的具体需求（基础、目标、时间等）
     等用户明确说"帮我规划一下"再调用路线专家
   - 输入：用户完整的学习需求
   - 输出：详细的分阶段学习路线

2. **code_review_expert**（代码审查专家）
   - 用途：当用户提交代码、要求审查代码质量、找 bug、优化性能时使用
   - 输入：完整代码内容 + 编程语言 + 可选额外要求
   - 输出：详细的代码审查报告

3. **search_expert**（联网搜索专家）
   - 用途：当用户需要查找最新信息、实时数据、新闻、技术文档时使用
   - 输入：搜索关键词/问题
   - 输出：整理好的搜索结果摘要 + 来源链接

## 工作原则

1. **理解需求**：先认真理解用户想要什么，再决定调用哪个工具或专家
2. **选对专家**：不同领域的问题派给对应专家，不要张冠李戴
3. **信息不足就问**：如果用户需求太宽泛、无法确定派哪个专家，可以向用户提问澄清
4. **结果整合**：拿到专家的结果后，整理成清晰、友好的回答给用户
5. **多步推理**：复杂问题可能需要多步调用（比如先搜索再做路线规划），
   你可以根据需要依次调用多个工具或专家
6. **诚实可信**：不知道的就说不知道，不要编造信息
7. **按需服务**：用户问多少答多少，不要过度输出。简单问题简单回答，复杂问题先确认需求再深入。
   - 用户说"想学XX" → 先简单介绍 + 问清楚基础/目标/时间
   - 用户说"帮我规划路线" → 才调用学习路线专家给详细方案
   - 用户发了一张图 → 才调用多模态专家

## 回答风格

- 像真人一样自然说话：简短、直接、口语化；不要自我介绍，不要列"我可以帮你 A/B/C"这类能力菜单。
- 用户只发一个字母或很短的内容时，用一句话自然接住（例如"是不是打字打岔了？直接说要干嘛就行"），不要道歉，不要解释你猜测的每一种可能。
- emoji 最多 1 个，能不用就不用。
- 不要默认使用标题、列表、加粗；只有内容真的需要分点才用。
- 调用子 Agent 后直接给结论，不要评价自己的回答（例如"这个规划思路很清晰"）。
- 若子 Agent（尤其是学习路线专家）已返回一份完整、详细的结果：最终回复应直接呈现这份结果的核心，不要再用一大段文字把它重述或总结一遍，避免"详细版+自己再写一份总结版"的重复。结果已经足够完整时，简短收尾即可（例如一句"路线已按 7 个阶段排好，需要我帮你细化某个阶段吗？"或直接结束），不要把它整体复述。
- 信息不足时，最多自然追问 1 个最关键的问题，不要一次抛出一串问题。

【关于澄清（人机交互）——分寸拿捏】
- 默认直接干，不要澄清。把"合理默认假设"当作你的第一选择。
- 只有同时满足以下三条才发起一次澄清：
  ① 关键信息确实缺失——缺了它，你完全无法做出有用的产出；
  ② 没有任何合理默认值可填——模型常识也无法替你补（比如方向/基础/目标冲突）；
  ③ 后果重且不可逆帮忙——产出的是长期路线/方案，猜错代价大，而非一次性小事。
- 具体、可执行、信息够的问题，一律直接执行：搜索资料、解释概念、改一处代码、问一句话，绝不清醒。
- 若要澄清：只问"最关键的一个问题"，给出 A/B/C 明确的选项，避免连环追问；
  用户已给的任何信息都要优先利用，不要重复问已明确的事。
- 连续澄清最多不超过 2 轮；还拿不准就用你已有的最佳默认值继续，不要无限问下去。
资源补充：
- 规划路线时，可调用搜索工具（tavily_search）查找真实、权威的课程/文档/教程
- 在路线的"学习资源推荐"部分附上这些资源的标题和网址（可直接访问的链接）
- 不要在路线里编造课程/教程名称——搜索不到就如实说明"暂未搜索到，可自行搜索"
"""

# ========== 同步总控缓存 ==========

_supervisor_agent = None
# 私有全局变量：缓存"不带 checkpointer"的总控 Agent 实例（同步入口用）
# 带 checkpointer 的异步实例单独缓存，见 _async_supervisor_agent

def build_supervisor_agent(checkpointer=None):
    """
    构建总控 Agent（单例：第一次调用创建，之后复用）

    参数:
        checkpointer: 可选的 LangGraph checkpointer（如 AsyncSqliteSaver）
                      传入则总控获得"暂停/恢复"能力（interrupt 前提）
                      不传则是普通图（同步 run_supervisor 用，保持兼容）

    流程:
        1. 初始化工具注册中心（注册所有默认工具组）
        2. 从注册中心拿全部工具
        3. 用 create_agent 生成总控 Agent graph
        4. 返回编译好的 Agent

    返回:
        编译好的 LangGraph Agent（可直接 invoke / ainvoke / astream）
        输入格式：{"messages": [{"role": "user", "content": "..."}]}
        输出格式：{"messages": [...]}，最后一条消息的 content 是最终回答
    """
    # 同步入口（不传 checkpointer）走单例缓存；异步入口（传了）走 _build_async
    if checkpointer is None:
        return _build_plain_supervisor()
        # 委托给"无 checkpoint 版"构建函数（保持原有单例行为）
    return _build_supervisor_with(checkpointer)
    # 有 checkpointer 时，新建一个带中断能力的图（不缓存，因 checkpointer 每次不同）

def _build_plain_supervisor():
    """构建无 checkpoint 的总控（同步入口用，原单例逻辑）"""
    global _supervisor_agent
    if _supervisor_agent is not None:
        return _supervisor_agent
        # 已建过，复用

    init_tools()
    # 注册子 Agent 工具组（幂等）

    registry = get_tool_registry()
    # 拿注册中心
    tools = registry.get_all_tools()
    # 全部工具 = sub_agents 组（3 个子 Agent 工具）

    llm = get_deepseek_llm(temperature=0.3)
    # 总控用 DeepSeek，低温度保证稳定决策

    _supervisor_agent = create_agent(
        model=llm,
        tools=tools,
        system_prompt=SUPERVISOR_SYSTEM_PROMPT,
    )
    return _supervisor_agent

def _build_supervisor_with(checkpointer):
    """构建带 checkpointer 的总控（异步入口用）
    每次调用新建（不缓存），因为传入的 checkpointer 是每次会话不同的连接
    """
    init_tools()
    # 确保工具已注册（幂等）

    registry = get_tool_registry()
    tools = registry.get_all_tools()
    # 同一批工具

    llm = get_deepseek_llm(temperature=0.3)

    return create_agent(
        model=llm,
        tools=tools,
        system_prompt=SUPERVISOR_SYSTEM_PROMPT,
        checkpointer=checkpointer,
        # 关键：把 checkpointer 传给 create_agent
        # 这样图有"记忆"，interrupt 能暂停并恢复
    )

# ========== 对外调用接口（同步 + 异步） ==========

def _extract_answer(result: dict) -> str:
    """
    从 create_agent 的返回结果中提取最终回答文本

    参数:
        result: agent.invoke() / ainvoke() 的返回值
                格式为 {"messages": [...消息列表...]}
                最后一条消息是最终回答（AIMessage）

    返回:
        str：最终回答的文本内容
    """
    messages = result.get("messages", [])
    # 取出消息列表

    if not messages:
        # 极端情况：消息列表为空，返回空串让上层兜底
        return ""

    last_msg = messages[-1]
    # 取最后一条消息（Agent 的最终回答）

    content = ""
    if hasattr(last_msg, "content"):
        # 如果是 AIMessage / BaseMessage 对象，用 .content 取文本
        content = last_msg.content
    elif isinstance(last_msg, dict):
        # 如果是字典（少见情况），取 content 字段
        content = last_msg.get("content", "")

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        # create_agent 的 AIMessage 可能是 content block 列表，只取文本块
        texts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text = part.get("text", "")
                if text:
                    texts.append(text)
            elif isinstance(part, str) and part.strip():
                texts.append(part)
        return "\n".join(texts)

    if content is None:
        return ""
    # 其它情况，直接转字符串
    return str(content)


def run_supervisor(user_input: str) -> str:
    """
    【同步】调用总控 Agent

    参数:
        user_input: 用户输入

    返回:
        总控的最终回答（字符串）
    """
    agent = build_supervisor_agent()
    # 拿到总控 Agent 实例（单例）

    # 构造消息（与子 Agent 保持一致：{"messages": [...]} 格式）
    messages = [{"role": "user", "content": user_input}]
    # create_agent 接收 messages 格式的输入

    result = agent.invoke({"messages": messages})
    # 同步调用
    # 返回格式：{"messages": [...]}

    return _extract_answer(result)
    # 从结果中提取最终回答文本


_async_supervisor_agent = None
# 异步总控的缓存标记：存"已建好的带 checkpoint 的总控 + 对应的 session 连接"
# 注意：checkpointer 连接是异步上下文（asynccontextmanager），不能跨调用裸缓存

async def arun_supervisor(user_input: str, thread_id: Optional[str] = None) -> str:
    """
    【异步】调用总控 Agent

    参数:
        user_input: 用户输入
        thread_id:  会话 ID（可选）
                    传入 → 总控挂 checkpointer，具备"暂停/恢复"能力（人机交互前提）
                    不传 → 普通异步调用（保持现有行为）

    返回:
        总控的最终回答（字符串）
    """
    # 构造消息（与同步版一致）
    messages = [{"role": "user", "content": user_input}]

    if thread_id is None:
        # 不带会话 ID：普通异步调用（不挂 checkpoint）
        agent = build_supervisor_agent()
        # 无 checkpoint 版单例
        result = await agent.ainvoke({"messages": messages})
        return _extract_answer(result)

    # ===== 带 thread_id：挂 checkpointer，获得暂停/恢复能力 =====
    from src.agents.checkpointer import get_checkpointer
    # 延迟导入，避免循环依赖
    # (checkpointer 里 import 了 base 的 ExecutionPlan，函数内导入更安全)

    # 用 async with 进入 SQLite checkpointer 连接上下文
    async with get_checkpointer() as checkpoint_saver:
        # get_checkpointer() 是 asynccontextmanager
        # 进入时建立异步 SQLite 连接，退出时自动关闭

        agent = _build_supervisor_with(checkpoint_saver)
        # 用这个会话的 saver 建带 checkpointer 的总控图

        config = {"configurable": {"thread_id": thread_id}}
        # LangGraph 运行配置：指定会话，interrupt 状态按会话隔离/恢复

        result = await agent.ainvoke(
            {"messages": messages},
            config=config,
            # 传入 config，图才有 thread_id，才能做暂停/恢复
        )
        return _extract_answer(result)

async def astream_supervisor(user_input: str, thread_id: Optional[str] = None):
    """
    【异步流式】调用总控 Agent，产出完整事件流
    """
    agent = build_supervisor_agent()

    messages = [{"role": "user", "content": user_input}]

    # ---- 内部 JSON 吞噬过滤器状态 ----
    # 作用：总控主模型会把"记忆更新 / 反思评分"等内部中间件产生的 JSON 一并复述到
    # 最终回答里（例如 {"should_update": true, ...}、{"scores": [...]}）。这些是给
    # 系统内部用的，绝不能推给前端。这里识别"以 { 开头、整体构成一个完整合法 JSON 对象"
    # 的文本块，然后整块丢弃；其余正常文本照常推送。
    _json_buf = ""       # 正在累积、疑似 JSON 的文本
    _in_json = False     # 是否已进入"疑似 JSON"状态
    _JSON_MAX = 100000   # 保留上限：累积超过此长度仍未识别为 JSON，则放弃并当作普通文本

    def _strip_internal_json(text: str) -> str:
        """剥离内部 JSON 块，返回应当推送的文本（内部 JSON 返回空串）。"""
        nonlocal _json_buf, _in_json

        if _in_json:
            # 已在"疑似 JSON"状态：继续累积，等它闭合
            _json_buf += text
            if len(_json_buf) > _JSON_MAX:
                # 太长还闭合不了 → 说明不是 JSON，当作普通文本放行一次
                _in_json = False
                buf = _json_buf
                _json_buf = ""
                return buf
            s = _json_buf.lstrip()
            if s.startswith("{") and s.endswith("}"):
                import json as _json
                try:
                    obj = _json.loads(s)
                except Exception:
                    obj = None
                if isinstance(obj, dict):
                    # 完整合法的 JSON 对象 → 内部输出，整体丢弃
                    _in_json = False
                    _json_buf = ""
                    return ""
            # 要么还没闭合，要么解析出来的不是对象 → 继续等，暂不输出
            return ""

        # 未在"疑似 JSON"状态：判断本次文本是否以 "{" 开头
        if text.lstrip().startswith("{"):
            # 可能是内部 JSON 的开始，进入吞噬模式
            _in_json = True
            _json_buf = text
            return ""
        # 普通文本，直接推送
        return text

    async for event in agent.astream_events(
        {"messages": messages},
        version="v1",
    ):
        event_type = event.get("event", "")
        event_name = event.get("name", "")
        data = event.get("data", {})

        if event_type == "on_tool_start":
            tool_name = event_name or "unknown_tool"
            tool_input = data.get("input", {})
            if isinstance(tool_input, dict):
                desc = (
                    tool_input.get("query")
                    or tool_input.get("question")
                    or tool_input.get("topic")
                    or tool_input.get("url")
                    or (str(list(tool_input.values())[0]) if tool_input else "")
                )
            else:
                desc = str(tool_input)[:50]
            yield {"type": "tool_start", "tool": tool_name, "content": desc}
            continue

        if event_type == "on_tool_end":
            tool_name = event_name or "unknown_tool"
            tool_output = data.get("output", "")
            if isinstance(tool_output, str):
                summary = tool_output[:80] + ("..." if len(tool_output) > 80 else "")
            else:
                summary = "完成"
            yield {"type": "tool_end", "tool": tool_name, "content": summary}
            continue

        if event_type == "on_chain_start":
            meaningful_names = [
                "supervisor", "learning_path", "code_review",
                "search", "clarify", "Agent",
            ]
            should_push = any(
                name.lower() in event_name.lower()
                for name in meaningful_names
            )
            if should_push:
                name_lower = event_name.lower()
                if "learning" in name_lower:
                    phase_text = "正在规划学习路线..."
                elif "code" in name_lower:
                    phase_text = "正在分析代码..."
                elif "search" in name_lower:
                    phase_text = "正在搜索资料..."
                elif "clarify" in name_lower:
                    phase_text = "正在梳理需求..."
                elif "supervisor" in name_lower or "agent" in name_lower:
                    phase_text = "正在分析你的需求..."
                else:
                    phase_text = f"正在处理：{event_name}"
                yield {"type": "thinking", "content": phase_text}
            continue

        # 推送文本，但先剥离内部 JSON（记忆更新 / 反思评分等），避免内部格式外泄
        if event_type == "on_chat_model_stream":
            chunk = data.get("chunk")
            if chunk is None:
                continue
            if hasattr(chunk, "content") and chunk.content:
                content = chunk.content
                if isinstance(content, str) and content:
                    clean = _strip_internal_json(content)
                    if clean:
                        yield {"type": "token", "content": clean}
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            text = part.get("text", "")
                            if text:
                                clean = _strip_internal_json(text)
                                if clean:
                                    yield {"type": "token", "content": clean}
            continue

        if event_type == "on_chat_model_end":
            continue

    yield {"type": "done"}