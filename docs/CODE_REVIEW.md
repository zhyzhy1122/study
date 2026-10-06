# 代码自查报告（CODE REVIEW）

> 结论先行：**流式输出存在一个会导致回答被截断的真实缺陷（P0），建议优先修复。**
>
> 本报告是在补齐 `tests/`（170 个离线用例）的过程中，通过"写测试倒逼读代码"得到的。
> 记录在这里有两个目的：一是留档待修，二是把"我知道自己的代码哪里有问题、以及为什么"
> 变成可讨论的工程判断，而不是等问题被面试官问出来。

---

## 一、P0：流式输出会被内部 JSON 截断

**位置**：`src/agents/supervisor.py` → `_strip_internal_json`

**现象**：该函数用 `buffer.endswith("}")` 判断"内部 JSON 是否结束"。当内部 JSON 的右花括号
和紧随其后的正文落在**同一个 chunk** 里时，例如：

```
'{"should_update": true}好的，你的学习路线如下……'
```

缓冲区变成 `'{"a": 1}好的…'`，永远不满足 `endswith("}")`，于是**后续所有 token 都被当作
待处理 JSON 一直缓冲下去**，用户看到的现象是回答在中间突然截断。

**影响**：用户可见的功能缺陷，且触发条件（模型把结构化输出和正文放在同一帧）相当常见。

**修复方向**：不要用 `endswith` 判断闭合，改为**花括号深度扫描**——从左到右计数 `{` / `}`，
深度归零即认定 JSON 结束，剩余部分作为正文吐给前端。同时给缓冲区加一个上限
（例如 2KB），超限就放弃过滤、原样输出，避免任何情况下把正文永久吞掉。

**已有回归测试**：`tests/test_supervisor_stream.py::test_stream_json_buffer_keeps_swallowing_text_after_closing_brace`
（该测试**固定了当前的错误行为**，修复后需要一并更新为期望"正常输出正文"。）

---

## 二、P1：反思中间件原地修改了存储层返回的对象

**位置**：`src/agents/middleware/memory_middleware.py` → `after_agent_async`

**现象**：

```python
old_profile = old_memories.get("profile", {})
old_profile.update(...)          # ← 直接改了 store 返回的 dict
```

**影响**：目前之所以没出问题，只是恰好因为每次调用都会重新从 SQLite 读一遍，拿到的是新对象。
一旦将来给 `memory/store.py` 加上缓存（很自然的优化），这里就会静默污染缓存内容，
出现"记忆串味"这种极难排查的问题。

**修复方向**：合并前 `copy.deepcopy()`，或改用 `{**old, **new}` 生成新对象。
`progress`、`notes` 有同样的写法，一并处理。

---

## 三、P1：`init_tools` 函数名遮蔽了同名子模块

**位置**：`src/modules/tools/__init__.py`

**现象**：包内同时存在子模块 `init_tools.py` 和一个同名函数 `init_tools`。由于 `__init__.py`
里 `from .init_tools import init_tools`，`import src.modules.tools.init_tools as m` 拿到的是
**函数**而不是**模块**，访问模块级状态（如 `_initialized`）会失败。

**影响**：可读性与可测试性受损；任何需要操作该模块状态的代码都必须绕道
`importlib.import_module`，属于隐性陷阱。

**修复方向**：把函数改名（如 `init_all_tools`）或把模块改名（如 `bootstrap.py`），
二者留一即可。

---

## 四、P1：反思重跑的边界语义不清晰

**位置**：`src/agents/base.py` → `run()` / `arun()` 的 `max_reflect` 循环

**现象**：

- `max_reflect=3` 时会发生 3 次重写，但**最后一版结果不会再被评估**；
  `after_agent` 只触发 3 次，却产生了 4 个输出。
- `max_reflect=0` 会完全跳过评估——语义上"不反思"，而不是"兜底失败"。

**影响**：调用方容易误解参数含义；"最后一次结果没有质量把关"也可能放走低质量回答。

**修复方向**：把语义写进 docstring 并统一为"最多评估 N+1 次、最多重写 N 次"，
或增加 `fail_closed` 开关。

---

## 五、P2：学习路线专家在同步链路上不可达

**位置**：`src/modules/learning_path/agent.py` → `_run`

**现象**：`_run` 在 `_aget_agent()` 执行前会抛 `RuntimeError`，因此
`run_supervisor()`（同步入口）和同步工具调用**永远走不到学习路线专家**，只有异步链路可用。

**影响**：功能在同步入口下静默缺失。要么补齐同步实现，要么在同步入口显式报"不支持"。

---

## 六、P2：`chat.py` 存在导入期副作用

**位置**：`src/api/routes/chat.py`

**现象**：模块导入时就 `mkdir data/uploads`、`mkdir data/exports` 并调度 `_init_conv_db()`，
且路径在导入时被固化为模块常量。

**影响**：路径无法在使用时重定向，测试与部署必须靠 monkeypatch 常量绕过。

**修复方向**：改为调用时惰性解析路径（函数内 `settings.project_root / "data"`）。

---

## 七、其他

- **Pydantic 弃用告警**：`src/config.py:24` 仍使用 v1 风格的 `class Config`，
  pydantic v2 下会告警，v3 将移除。建议迁移为 `model_config = SettingsConfigDict(...)`。
  这是 170 个用例运行时唯一的告警，容易清理。
- **CORS 全开放**：`allow_origins=["*"]` 与 `allow_credentials=True` 同时开启是开发期配置，
  生产环境应收敛为具体域名。

---

## 八、尚未被离线测试覆盖的部分

| 未覆盖 | 原因 |
|---|---|
| 真实 LLM 的工具选择质量（总控是否真的选对子 Agent） | 需要真实模型，测试中用替身替换，只验证了工具装配与调度契约 |
| `/api/chat`、`/api/chat/stream` 的正常路径 | 会触发真实模型调用；当前只覆盖了请求体校验（422） |
| MCP 传输层（`build_mcp_connections` / `load_mcp_tools`） | 会 spawn `npx tavily-mcp`、`@playwright/mcp`，不适合放进单测 |
| `/api/conversations*`、`/api/upload/image` | 会写 `conversations.db` 与上传目录；为保持测试只读而跳过 |
| `/api/workspace/choose` | 弹原生 tkinter 目录选择框，无法 headless 运行 |
| `AsyncSqliteSaver` checkpointer 端到端 | 需要真实 `data/checkpoints.db`；当前只断言了调用契约 |

---

## 九、修复优先级建议

1. **P0 流式 JSON 截断** —— 用户可见功能缺陷，先修。
2. **P1 记忆中间件原地修改** —— 定时炸弹，趁现在只有一处调用点，改动成本最低。
3. **P1 `init_tools` 命名遮蔽** —— 纯重命名，风险低收益明确。
4. **P1 反思边界语义** —— 需要先确定期望行为，再动代码。
5. P2 两项可并入后续迭代。
