# -*- coding: utf-8 -*-
"""
src/modules/code_review/agent.py
作用：代码审查 Agent（真实版）
功能：
  - 分析代码，找 bug 和逻辑错误
  - 给出优化建议（性能、可读性、最佳实践）
  - 输出结构化的审查结果
技术：
  - 用 LangChain 的 create_agent 构建
  - DeepSeek 模型做分析
  - 暂时不带工具（纯分析，后面再加 MCP 搜索）
"""

# ========== 导入部分 ==========

from langchain.agents import create_agent
# 从 langchain.agents 导入 create_agent
# 作用：LangChain v1 新标准的 Agent 构造函数
# 传入模型、工具、系统提示词，就能创建一个能思考、能调用工具的 Agent

from src.agents.base import BaseAgent
# 导入 Agent 基类
# 代码审查 Agent 继承它，获得中间件支持

from src.utils.llm import get_deepseek_llm
# 导入 DeepSeek 模型工厂函数

# ========== 系统提示词 ==========

CODE_REVIEW_SYSTEM_PROMPT = """你是一位资深的代码审查专家，经验丰富、严谨细致。

你的任务是审查用户提供的代码，找出问题并给出优化建议。
直接进入审查，不要自我介绍、不要菜单式开场白。

审查维度：
1. **Bug 和逻辑错误**：语法错误、逻辑漏洞、边界条件、空指针、异常处理
2. **性能问题**：不必要的循环、内存泄漏、低效算法
3. **代码质量**：命名不规范、可读性差、重复代码、结构混乱
4. **安全问题**：注入风险、敏感信息泄露、不安全的函数
5. **最佳实践**：是否符合语言最佳实践、是否有更好的写法

输出格式（必须严格按以下 Markdown 格式输出）：

## 🔍 代码审查结果

### 📋 总体评价
（用 2-3 句话概括代码的整体质量和主要问题）

### ⚠️ 发现的问题
（按严重程度从高到低排列）

1. **[严重程度] 问题标题**
   - **位置**：第 X 行 / 函数名
   - **问题描述**：详细说明问题是什么、为什么有问题
   - **修复建议**：具体说明怎么改

2. **[严重程度] 问题标题**
   - ...（同上）

### 💡 优化建议
1. **优化点标题**
   - **描述**：为什么要优化
   - **建议**：具体的优化方案

### ✅ 优化后的代码
```python
（完整的优化后代码，确保可以直接运行）
```

### 📌 总结
（用 1-2 句话总结最关键的改进点）

注意事项：
- 严重程度分为：🔴 严重 / 🟡 中等 / 🟢 建议
- 如果代码没有明显问题，也要给出优化建议和最佳实践
- 优化后的代码要保持原有功能不变，只是改进质量
- 用中文回答，语言专业但易懂
- 不要输出多余的解释，严格按上面的格式输出
"""

# CODE_REVIEW_SYSTEM_PROMPT：代码审查 Agent 的系统提示词
# 告诉模型它的角色、要做什么、输出格式是什么
# 系统提示词是 Agent 的"灵魂"，写得好不好直接影响输出质量

# ========== 代码审查 Agent 类 ==========

class CodeReviewAgent(BaseAgent):
    """
    代码审查 Agent（真实版）
    继承自 BaseAgent，拥有中间件支持
    内部用 create_agent 构建真正的智能体
    """

    def __init__(self):
        """构造函数"""
        super().__init__(name="code_review")
        # 调用父类构造函数，设置 name 为 "code_review"

        self._agent = None
        # _agent 属性：内部的 create_agent 实例
        # 用下划线开头表示"内部属性"，外部不应该直接访问
        # 懒加载：第一次用的时候才创建，避免启动慢

    def _get_agent(self):
        """
        获取内部的 create_agent 实例（懒加载）
        第一次调用时创建，之后直接返回缓存的实例

        返回:
            create_agent 创建的 Agent 实例
        """

        if self._agent is None:
            # 如果还没创建过，就创建一个
            llm = get_deepseek_llm(temperature=0.2)
            # 获取 DeepSeek 模型实例
            # 代码审查要严谨，温度设 0.2（很低），减少随机性

            self._agent = create_agent(
                model=llm,
                # 模型实例

                tools=[],
                # 工具列表：暂时为空（后面加 MCP 工具）
                # create_agent 即使没工具也能用，就是纯对话 Agent

                system_prompt=CODE_REVIEW_SYSTEM_PROMPT,
                # 系统提示词：定义 Agent 的角色和输出格式
            )
            # create_agent 创建 Agent
            # 这是 LangChain v1 的标准用法

        return self._agent
        # 返回 Agent 实例

    def _run(self, user_input: str, before_ctx: dict = None, **kwargs) -> str:
        """
        执行代码审查（BaseAgent 要求重写的方法）

        参数:
            user_input: 用户输入（代码 + 描述）
            before_ctx: 中间件的 before 上下文
            **kwargs:   额外参数

        返回:
            审查结果（Markdown 格式字符串）
        """

        agent = self._get_agent()
        # 获取 create_agent 实例（懒加载）

        # 构造用户消息
        # 把用户输入包装成 messages 格式
        messages = [
            {"role": "user", "content": user_input}
        ]
        # messages 是一个列表，每个元素是一条消息
        # role 是 "user" 表示用户说的话，content 是消息内容
        # create_agent 的 invoke 接收 messages 格式的输入

        # 调用 Agent
        result = agent.invoke({"messages": messages})
        # invoke 是调用 Agent 的方法
        # 传入 {"messages": messages}，Agent 就会处理这些消息
        # result 是 Agent 的最终状态，包含 messages 等信息

        # 从结果中取出最后一条消息的内容（就是 Agent 的最终回答）
        final_answer = result["messages"][-1].content
        # result["messages"] 是所有消息的列表（包括用户的和模型的）
        # [-1] 取最后一条（就是 Agent 的最终回答）
        # .content 取消息内容

        return final_answer
        # 返回审查结果

    async def _arun(self, user_input: str, before_ctx: dict = None, **kwargs) -> str:
        """
        【异步】执行代码审查

        参数:
            user_input: 用户输入（代码 + 描述）
            before_ctx: 中间件的 before 上下文
            **kwargs:   额外参数

        返回:
            审查结果（Markdown 格式字符串）
        """

        agent = self._get_agent()
        # 获取 create_agent 实例

        # 构造消息
        messages = [
            {"role": "user", "content": user_input}
        ]

        # 异步调用 Agent
        result = await agent.ainvoke({"messages": messages})
        # ainvoke：agent.invoke 的异步版本
        # 用 await 等待结果，不会阻塞事件循环
        # create_agent 创建的 Agent 天生支持 ainvoke

        # 取出最后一条消息的内容
        final_answer = result["messages"][-1].content

        return final_answer
        # 返回审查结果

# ========== 文件结束 ==========
