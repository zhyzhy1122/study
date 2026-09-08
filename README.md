# 学习之家 AI · Atlas 学习工作台

> 一个基于多智能体协作的 AI 学习工作台——总控 Agent 调度多个领域专家，帮你规划学习路线、审查代码、诊断报错。

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-green)
![LangGraph](https://img.shields.io/badge/LangGraph-0.2%2B-orange)
![License](https://img.shields.io/badge/License-MIT-yellow)

---

## 一句话定位

**总控 Agent + 领域专家 Agent 的多智能体学习平台。** 你说想学什么 / 遇到什么问题，总控先做意图分析和任务规划，再把任务分发给对应的专家 Agent 执行，最后汇总结果流式返回给你。

不是一个大而全的单体 Agent，而是按职责拆分的多智能体协作系统——每个专家只带自己的工具和知识，上下文小而专注，准确率更高，也方便独立迭代。

---

## 能做什么

### 📚 学习路径规划（核心模块）
- 输入想学的技术，输出分阶段学习路线（入门 → 进阶 → 高级 → 实战）
- 生成 2-3 条不同风格的路线供选择（Human-in-the-loop）
- 每个阶段标注学习难点
- Mermaid 学习路线图可视化
- 联网搜索多平台学习资源（B站 / GitHub / 官方文档）
- 支持导出 Word / Markdown

### 🔍 代码审查助手
- 粘贴代码 → 自动找 bug、给优化建议
- 上传报错截图 → 多模态识别错误信息 → 给出修复方案
- 联网搜索 Stack Overflow / 官方文档补充解决方案

### 🧠 贯穿全程的能力
- **中间件机制**：4 个钩子点（调 Agent 前后 / 调模型前后），日志、评估、记忆等横切逻辑可插拔
- **反思评估闭环**：LLM-as-a-Judge 五维打分，不达标自动打回重跑（最多 3 次）
- **记忆持久化**：checkpointer（短期状态）+ store（长期记忆），多 SQLite 库分域存储
- **流式输出**：SSE 实时推 token，边生成边看
- **输入重写**：多轮对话中自动补全指代词和省略内容

---

## 系统架构

```
用户输入（文字 / 图片）
  │
  ▼
┌──────────────────────────────────┐
│   【总控 Agent · Supervisor】     │
│   Plan-and-Execute 模式           │
│   1. 理解意图                     │
│   2. 生成执行计划                 │
│   3. 调度执行（串行 / 并行混合）   │
└───────────┬──────────────────────┘
            │
    ┌───────┴───────┐
    ▼               ▼
┌─────────┐   ┌──────────┐
│ 代码审查 │   │ 学习规划 │
│  模块    │   │   模块    │
│ · 多模态 │   │ · 路线生成│
│ · 代码分析│   │ · 资源搜索│
│ · 联网搜  │   │ · 路线图  │
│         │   │ · 导出    │
└─────────┘   └──────────┘
            │
            ▼
┌──────────────────────────────────┐
│  【中间件层 · 4 个钩子点】         │
│  before_agent → before_llm        │
│       ↓                ↓          │
│  after_agent ← after_llm          │
│  （反思评估挂在 after_agent）      │
└──────────────────────────────────┘
            │
            ▼
      流式输出 + 记忆存档
```

---

## 技术栈

| 层 | 技术 | 说明 |
|----|------|------|
| Agent 框架 | LangChain `create_agent` | 新标准 API |
| 工作流编排 | LangGraph + Plan-and-Execute | 规划先行 + 串行/并行混合调度 |
| 文本模型 | DeepSeek（deepseek-v4-flash） | 规划 / 审查 / 反思等文字任务 |
| 多模态模型 | 通义千问 Qwen-VL-Max | 图片识别（DashScope 调用） |
| 工具接入 | MCP 协议 + `langchain-mcp-adapters` | Tavily 搜索等 |
| 记忆 | langgraph-checkpoint-sqlite + 自研 store | 短期状态 + 长期记忆，多库分域 |
| 后端 | FastAPI + SSE | 端口 8000，流式输出 |
| 前端 | 原生 HTML/CSS/JS（Atlas 界面） | vendor 本地化（marked / mermaid / highlight） |
| 文档导出 | python-docx | Word / Markdown |
| 配置管理 | pydantic-settings | 环境变量 + `.env` |

---

## 快速开始

### 环境要求
- Python 3.10+
- DeepSeek API Key（文本模型）
- 阿里云 DashScope API Key（多模态模型，可选，不用图片功能可以不配）

### 1. 克隆项目
```bash
git clone <your-repo-url>
cd 学习之家
```

### 2. 创建虚拟环境并安装依赖
```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### 3. 配置环境变量
在项目根目录创建 `.env` 文件：
```env
# 必填
DEEPSEEK_API_KEY=your_deepseek_api_key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash

# 可选（多模态功能需要）
DASHSCOPE_API_KEY=your_dashscope_api_key
QWEN_VL_MODEL=qwen-vl-max

# 可选（联网搜索需要）
TAVILY_API_KEY=your_tavily_api_key
```

### 4. 启动后端
```bash
python run_api.py
# 或：uvicorn src.api.app:app --host 0.0.0.0 --port 8000 --reload
```

后端启动后访问 `http://localhost:8000/docs` 查看 API 文档。

### 5. 打开前端
后端启动后，前端已经通过 FastAPI 的静态文件服务挂载好了，浏览器访问：

```
http://localhost:8000/static/index.html
```

> **注意**：不要直接双击 `frontend/index.html` 打开——那样是 `file://` 协议，前端用的是相对路径 `/api/...`，会因为跨域（CORS）导致接口请求和 SSE 流式推送全部失败。必须通过后端同源访问。

---

## 项目结构

```
学习之家/
├── frontend/              # 前端（原生 HTML/CSS/JS）
│   ├── index.html
│   ├── app.js
│   ├── style.css
│   └── vendor/           # 第三方库本地化（marked / mermaid / highlight）
├── src/
│   ├── agents/           # Agent 层
│   │   ├── base.py       # BaseAgent 基类
│   │   ├── supervisor.py # 总控 Agent
│   │   ├── checkpointer.py
│   │   └── middleware/   # 中间件（日志 / 记忆 / 反思评估）
│   ├── modules/          # 领域模块
│   │   ├── code_review/  # 代码审查
│   │   ├── learning_path/# 学习路径规划
│   │   ├── clarify/      # 需求澄清
│   │   ├── rewriter/     # 输入重写
│   │   ├── search/       # 联网搜索
│   │   ├── multimodal/   # 多模态识别
│   │   └── tools/        # 工具注册与 MCP 接入
│   ├── memory/           # 记忆层
│   │   ├── store.py      # 长期记忆存储
│   │   └── messages.py
│   ├── api/              # API 层（FastAPI）
│   │   ├── app.py
│   │   ├── deps.py
│   │   └── routes/
│   ├── schema/           # 数据模型（Pydantic）
│   ├── utils/
│   │   └── llm.py        # LLM 实例统一管理
│   └── config.py         # 配置中心
├── data/                 # 运行时数据（数据库、导出文件等）
│   ├── checkpoints.db
│   ├── messages.db
│   ├── memory.db
│   └── conversations.db
├── requirements.txt
├── run_api.py           # 后端启动脚本
└── 项目设计备忘录.md     # 完整设计文档
```

---

## Agent 分工

| Agent | 职责 | 模型 | 模块 |
|-------|------|------|------|
| **总控（Supervisor）** | 意图分析、生成执行计划、调度各 Agent、汇总结果 | DeepSeek | 全局 |
| **学习规划 Agent** | 生成学习路线、难点标注、资源推荐 | DeepSeek | 学习规划 |
| **代码审查 Agent** | 分析代码、找 bug、给优化建议 | DeepSeek | 代码审查 |
| **多模态识别 Agent** | 识别图片中的代码 / 报错信息 | Qwen-VL-Max | 代码审查 |
| **搜索 Agent** | 联网搜索解决方案 / 学习资源 | DeepSeek + MCP | 共用 |
| **输入重写器** | 短句补全、指代消解（总控前处理） | DeepSeek | 全局 |
| **澄清模块** | 需求宽泛时抛选项让用户选（Human-in-the-loop） | 无 LLM | 全局 |

---

## 核心机制详解

### 中间件机制（洋葱模型）

四个钩子点，按洋葱模型顺序执行：

```
请求 → before_agent → before_llm → [核心LLM调用] → after_llm → after_agent → 响应
```

- **before_agent**：Agent 执行前（上下文注入、参数校验）
- **before_llm**：调模型前（提示词增强、成本预估）
- **after_llm**：调模型后（结果校验、token 统计）
- **after_agent**：Agent 执行后（反思评估、记忆写入、日志记录）

好处：横切逻辑从业务代码剥离，中间件可插拔、可复用，Agent 本身只关心业务。

### 反思评估闭环

挂在 `after_agent` 钩子上，用 LLM-as-a-Judge 对回答五维打分：

| 维度 | 说明 |
|------|------|
| Accuracy | 事实准确性 |
| Completeness | 完整性（有没有遗漏） |
| Clarity | 表达清晰度 |
| Helpfulness | 实际有用性 |
| Safety | 安全性（有无错误/有害内容） |

**打回规则**：任一维度 < 3 分 或 总分 < 20 分 → 打回重跑，最多 3 次。每次重跑附带评审意见，让 Agent 针对性改进。

### 记忆持久化

按用途分库存储，避免单库膨胀：
- `checkpoints.db`：LangGraph 执行状态快照（用于恢复 / 中断续跑）
- `messages.db`：原始对话消息
- `memory.db`：沉淀的长期记忆（用户偏好、知识点等）
- `conversations.db`：会话元数据（标题、创建时间等）

---

## 开发说明

### 新增一个专家 Agent
1. 在 `src/modules/` 下新建模块目录
2. 继承 `BaseAgent` 基类，实现 `run()` 或 `stream()` 方法
3. 在总控的调度表里注册新 Agent 和它能处理的意图
4. 需要中间件的话，在 Agent 上挂对应中间件即可

### 新增一个中间件
1. 在 `src/agents/middleware/` 下新建文件
2. 实现对应钩子的方法（`before_agent` / `before_llm` / `after_llm` / `after_agent`）
3. 在需要的 Agent 上注册，或者全局注册到所有 Agent

---

## 已知限制与后续规划

- [ ] PDF 格式导出（当前仅 Word + Markdown）
- [ ] Docker 部署
- [ ] 学习进度追踪 / 打卡
- [ ] 路线动态调整（根据学习反馈）
- [ ] 知识图谱可视化（Graphviz）

---

## License

MIT
