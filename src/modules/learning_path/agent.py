# -*- coding: utf-8 -*-
"""
src/modules/learning_path/agent.py
作用：学习路径规划 Agent（核心版）
功能（P0 核心）：
  - 分阶段学习路线生成（入门 → 基础 → 进阶 → 高级 → 实战）
  - 每个阶段标注：知识点列表 + 学习目标 + 预计时长 + 重点难点
  - 结构化 Markdown 输出
技术：
  - 用 create_agent 构建（后面加工具/多轮对话方便）
  - DeepSeek 模型
  - 暂时不带工具（后面加 MCP 搜索资源）
"""

# ========== 导入部分 ==========

from langchain.agents import create_agent
# 导入 create_agent：LangChain v1 新标准 Agent 构造函数

from src.agents.base import BaseAgent
# 导入 Agent 基类，继承它获得中间件支持

from src.utils.llm import get_deepseek_llm
# 导入 DeepSeek 模型工厂函数

from src.modules.tools import get_tool_registry, init_mcp_tools
# 新增：从工具注册中心导入
# get_tool_registry：拿注册中心单例（按组取工具）
# init_mcp_tools：异步初始化 MCP 工具（连接 Tavily server → 工具注册进中心）

# ========== 工具组声明 ==========
LEARNING_PATH_TOOL_GROUPS = ["mcp_search"]
# learning_path Agent 能调的 MCP 工具组
# 挂 mcp_tavily：让路线规划能联网搜索真实课程/文档资源
# 为什么按"组名"取：和 SearchAgent 一致从注册中心取，Agent 不关心工具细节

# ========== 系统提示词 ==========

LEARNING_PATH_SYSTEM_PROMPT = """你是一位资深的技术学习规划师，擅长为不同基础的学习者制定科学、系统的学习路线。

你的任务是根据用户想学的技术和自身情况，生成一份学习路线。

⚠️ 重要原则：按需输出，不要过度服务
- 如果用户只是简单提到想学某技术（如"我想学C++"）、没有明确要完整路线，先不要输出完整的分阶段路线
- 先做 2-3 句自然回应：这门技术大概是什么、学起来什么感觉、大致分几个阶段
- 然后只自然追问 1-2 个最关键的问题（比如现在是什么基础、目标是找工作还是兴趣），不要一次抛 3 个，也不要把问题列成清单式
- 只有当用户明确说"给我规划路线"、"详细一点"、"分阶段"等，再输出完整的详细路线
- 核心：用户要多少给多少，别一下子把人吓跑

规划原则：
1. **循序渐进**：从易到难，每个阶段为下一个阶段打基础
2. **实用导向**：重点学工作中真正用得到的，不堆砌无关内容
3. **可执行性**：每个阶段有明确的知识点、学习目标和预计时间
4. **难点标注**：明确指出每个阶段的重点和难点，以及学习建议

输出格式（必须严格按以下 Markdown 格式输出）：

# 📚 学习路线：{技术名称}
## 🗺️ 路线总览图
（用 mermaid flowchart 画出各阶段的先后关系，每个阶段用一句话概括核心内容）
```mermaid
## 🎯 适合人群
（一句话说明这份路线适合什么样基础的人）

## ⏱️ 总预计学习时长
（总时长，比如 "3-4 个月（每天 2 小时）"）

---

## 阶段一：入门阶段（{预计时长}）

### 🎯 学习目标
（本阶段学完能达到什么水平）

### 📖 核心知识点
1. 知识点 1
2. 知识点 2
3. ...

### ⚠️ 重点难点
- **重点**：...
- **难点**：...
- **学习建议**：...

### ✅ 验收标准
（怎么验证自己学完了这个阶段）

---

（阶段二、三、四... 同上格式）

---

## 📌 学习建议
1. 建议 1
2. 建议 2
3. 建议 3

## 🎯 学习资源推荐方向
（说明应该找什么样的资源来学，不用列具体链接）
- 入门：推荐看什么类型的教程
- 进阶：推荐看什么类型的文档/书籍
- 实战：推荐做什么类型的项目

注意事项：
- 用中文回答
- 至少分 4-5 个阶段（入门 → 基础 → 进阶 → 高级 → 实战）
- 每个阶段的知识点要具体，不能太笼统
- 预计时长要合理，不要太夸张
- 重点难点要真实，是新手真的会卡住的地方
- 严格按上面的格式输出，不要多加额外的章节
- 路线总览图必须用 mermaid flowchart 绘制，语法要正确（能用 mermaid 渲染器正常显示）
资源补充原则：
- 规划路线时，可调用搜索工具（tavily_search）查找真实、权威的课程/文档/教程
- 在"学习资源推荐"部分附上这些资源的标题和网址（可直接访问的链接）
- 不要在路线里编造课程/教程名称——搜索不到就如实说明"暂未搜索到，可自行搜索"
"""

# LEARNING_PATH_SYSTEM_PROMPT：学习规划的系统提示词
# 告诉模型它的角色、规划原则、输出格式

# ========== 学习规划 Agent 类 ==========

class LearningPathAgent(BaseAgent):
    """
    学习路径规划 Agent（核心版）
    继承自 BaseAgent，拥有中间件支持
    内部用 create_agent 构建
    """

    def __init__(self):
        """构造函数"""
        super().__init__(name="learning_path")
        # 调用父类构造函数，设置 name 为 "learning_path"

        self._agent = None
        # 内部的 create_agent 实例，懒加载

    async def _aget_agent(self):
        """
        【异步】获取内部的 create_agent 实例（懒加载 + 首次自动初始化 MCP）

        返回:
            create_agent 实例

        做法:
            - 首次调用: await init_mcp_tools()（连接 Tavily → 工具注册进中心）
                       再从注册中心取 mcp_tavily 组工具，create_agent(tools=[...]) 构建
            - 之后调用: 直接返回缓存 self._agent，不重复初始化
        """
        if self._agent is not None:
            # 已建好，复用
            return self._agent

        # 首次：确保 MCP 工具已初始化（幂等，重复调用安全）
        await init_mcp_tools()
        # init_mcp_tools：连接 Tavily MCP server + 自动发现工具 → 注册进注册中心
        # 幂等：已初始化过直接返回 0，不重复连 server

        # 从注册中心取"本 Agent 要的 MCP 工具组"
        registry = get_tool_registry()
        # 拿注册中心单例
        tools = []
        for group in LEARNING_PATH_TOOL_GROUPS:
            tools.extend(registry.get_tools(group))
            # get_tools("mcp_tavily")：取该组全部工具（tavily_search）
            # 并把它们并进本 Agent 的 tools

        llm = get_deepseek_llm(temperature=0.7)
        # 学习规划保持 0.7 温度（创造性）

        self._agent = create_agent(
            model=llm,
            tools=tools,
            # 工具：从注册中心取的 MCP 搜索工具（不再是空的）
            # 现在 Agent 能联网搜索真实资源
            system_prompt=LEARNING_PATH_SYSTEM_PROMPT,
            # 系统提示词（稍后加"搜资源"指引）
        )
        return self._agent

    def _run(self, user_input: str, before_ctx: dict = None, **kwargs) -> str:
        """
        【同步】执行学习路径规划（弱化版）
        注意：MCP 初始化是异步的，同步入口不负责初始化
        只有当 agent 已缓存（之前异步初始化过）时才可用

        参数:
            user_input: 用户输入（想学什么 + 自身情况）
            before_ctx: before 中间件上下文
            **kwargs:   额外参数

        返回:
            学习路线（Markdown 字符串）
        """
        if self._agent is None:
            # 缓存里还没有 agent（没做过异步初始化）
            # 抛明确错误，引导走异步路径
            raise RuntimeError(
                "LearningPathAgent 同步调用时 MCP 工具尚未初始化，"
                "请先用异步入口 arun() 初始化一次"
            )

        agent = self._agent
        # 用已缓存的 agent

        messages = [{"role": "user", "content": user_input}]

        result = agent.invoke({"messages": messages})
        # 同步调用
        final_answer = result["messages"][-1].content
        return final_answer
        # 返回学习路线

    async def _arun(self, user_input: str, before_ctx: dict = None, **kwargs) -> str:
        """
        【异步】执行学习路径规划（主链路）

        参数:
            user_input: 用户输入（想学什么 + 自身情况）
            before_ctx: before 中间件上下文
            **kwargs:   额外参数

        返回:
            学习路线（Markdown 字符串）
        """
        agent = await self._aget_agent()
        # 异步懒加载（首次会 await init_mcp_tools 初始化 MCP 工具）

        messages = [{"role": "user", "content": user_input}]

        result = await agent.ainvoke({"messages": messages})
        # 异步调用
        final_answer = result["messages"][-1].content
        return final_answer

# ========== 文件结束 ==========
