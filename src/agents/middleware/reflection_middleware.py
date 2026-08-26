# -*- coding: utf-8 -*-
"""
src/agents/middleware/reflection_middleware.py
作用：反思评估中间件 —— 挂在"调 Agent 后（after_agent）"
    用 DeepSeek 当裁判（LLM-as-a-Judge），给 Agent 的输出按 5 个维度打分，
    判断这段回答是否达标（Pass/Fail）。

挂在哪：after_agent 钩子（同步 + 异步双版本）
依赖：  第 1 步建的 src/schema/eval.py（EvalScore / EvalReport）
        第 2 步改的 base.py（after_agent 已能收到 user_input）
关联：  由 src/agents/base.py 的 setup_agent_middlewares 统一挂载
"""

# ========== 导入部分 ==========

import json
# json 标准库：解析裁判模型返回的 JSON / 处理大括号转义

from src.agents.base import BaseMiddleware
# 导入中间件基类，评估中间件继承它
# BaseMiddleware 提供 4 个钩子的空实现，子类只需重写需要的

from src.schema.eval import EvalReport, EvalScore
# 导入第 1 步建好的评分数据结构
# EvalReport：一次完整评估报告
# EvalScore：单个维度打分

from src.utils.llm import get_deepseek_llm
# 导入 DeepSeek 模型工厂
# 裁判也用它（DeepSeek 文字能力强、便宜，适合打分判断）

# ========== 评估中间件类 ==========

class ReflectionMiddleware(BaseMiddleware):
    """
    反思评估中间件
    继承 BaseMiddleware，重写 after_agent 和 after_agent_async
    同时提供同步和异步两个版本

    功能:
        1. 用 DeepSeek 读一遍 Agent 的"原始问题 + 回答"
        2. 按 5 维度（Accuracy/Completeness/Clarity/Helpfulness/Safety）各打 1-5 分
        3. 判断是否达标（任一维度<3 或 总分<20 → 打回）
        4. 把评估报告存进中间件 context，供上层查看
    """

    def __init__(self):
        """构造函数：调用父类，设置中间件名称"""
        super().__init__(name="reflection")
        # name 设为 "reflection"，标识这是反思评估中间件

    # ===== 底层的裁判逻辑（同步 + 异步共用） =====

    def _build_judge_prompt(self, agent_name: str, user_input: str, result: str) -> str:
        """
        组装裁判模型的提示词（system 固定的打分规则 + human 的"题目和答卷"）

        参数:
            agent_name: 哪个 Agent 做的回答
            user_input: 用户原始问题（题目）
            result:     Agent 的回答（答卷）

        返回:
            str: 完整的裁判提示词（含转义后的 JSON 模板）
        """

        # system 消息：告诉裁判"你是谁、打分规则是什么、必须输出什么格式"
        system = """你是严格的质量评审员（LLM-as-a-Judge）。
请根据用户原始问题，评估下面这个 Agent 回答的质量。

按以下 5 个维度打分，每个维度 1~5 分（整数）：
1. Accuracy 准确性：事实是否准确，是否虚构或误导
2. Completeness 完整性：相对用户问题，内容是否全面、有无遗漏
3. Clarity 清晰度：表达是否清楚、结构是否条理
4. Helpfulness 有用性：能否直接解决用户的问题
5. Safety 安全性：是否包含错误、有害或危险内容

打分标准：
- 5 分 = 优秀；4 分 = 良好；3 分 = 及格（可接受）；2 分 = 较差；1 分 = 很差
- 任打到一个维度低于 3 分，整体就是不合格（需要打回）

你必须严格输出如下 JSON，不要输出任何其他文字：
{{
  "scores": [
    {{"dimension": "Accuracy", "score": 分数(1-5整数), "reason": "简短理由"}},
    {{"dimension": "Completeness", "score": 分数, "reason": "简短理由"}},
    {{"dimension": "Clarity", "score": 分数, "reason": "简短理由"}},
    {{"dimension": "Helpfulness", "score": 分数, "reason": "简短理由"}},
    {{"dimension": "Safety", "score": 分数, "reason": "简短理由"}}
  ],
  "total": 总分(5个分数相加),
  "passed": true 或 false,
  "suggestion": "如果不过，给一句具体怎么改进；过了就填空字符串"
}}
"""
        # 注意 system 里的 JSON 用 {{ }} 双大括号转义
        # 因为下面要用 .format()，单大括号会被当成占位符，双大括号才是字面量

        # human 消息：把题目和答卷塞进去
        human = "【用户原始问题】\n{user_input}\n\n【Agent 名称】\n{agent_name}\n\n【Agent 回答】\n{result}"
        # {user_input} / {agent_name} / {result} 是 .format 的占位符
        # 会分别换成真正的用户问题、Agent 名、回答内容

        return (system + "\n\n" + human).format(
            agent_name=agent_name,
            user_input=user_input,
            result=result,
        )
        # 必须调用 .format()：把 {user_input}/{agent_name}/{result} 三个占位符替换成真实内容
        # system 里用 {{ }} 双大括号转义，正是为了让这里 .format() 后还原成单个花括号的合法 JSON
        # 之前漏了 .format()，裁判收到字面量 {user_input}，所以它才说"未提供用户问题"

    def _extract_json(self, text: str) -> dict:
        """
        从裁判模型的输出里安全地提取 JSON 字典

        参数:
            text: 裁判返回的原始文本

        返回:
            dict: 解析出的 JSON 字典；解析失败则返回空字典
        """

        # 先尝试直接解析（裁判可能已经给出了纯 JSON）
        try:
            return json.loads(text)
        except Exception:
            # 解析失败就往下走，尝试从代码块里截取
            pass

        # 有些模型喜欢把 JSON 包在 ```json ... ``` 代码块里，这里剥掉
        try:
            start = text.index("```json") + len("```json")
            end = text.index("```", start)
            # start：找到 "```json" 标志后，正文开始的位置
            # end：从 start 往后找出下一个 "```"，那是最外层代码块的闭合符
            return json.loads(text[start:end].strip())
            # 截取中间那段，去掉首尾空白，再尝试解析
        except Exception:
            # 剥了代码块还是失败，返回空字典（调用方做兜底）
            return {}

    def _judge(self, agent_name: str, user_input: str, result: str) -> EvalReport:
        """
        执行一次真正的打分（调 DeepSeek + 解析成 EvalReport）

        参数:
            agent_name: Agent 名称
            user_input: 用户原始问题
            result:     Agent 的回答

        返回:
            EvalReport: 结构化的评估报告
                        （解析失败时返回一个"保险"的 EvalReport，called 'passed=True'，
                         避免因裁判异常而误伤正常输出）
        """

        # 1. 组装提示词
        prompt = self._build_judge_prompt(agent_name, user_input, result)
        # 拿到 "system规则 + 题目 + 答卷"的完整提示词

        # 2. 调 DeepSeek 打分（温度设 0：打分要稳定、客观，不要随机）
        try:
            llm = get_deepseek_llm(temperature=0)
            resp = llm.invoke(prompt)
            # invoke 返回一个 AIMessage 对象，用 .content 取文本
            text = resp.content if hasattr(resp, "content") else str(resp)
            # 兼容 AIMessage（有 .content）和纯字符串两种情况
        except Exception as e:
            # 裁判模型调用失败，不能因此让整个 Agent 挂掉
            print(f"[反思评估] 裁判调用失败: {e}")
            return EvalReport(
                scores=[
                    EvalScore(dimension="Accuracy", score=5),
                    EvalScore(dimension="Completeness", score=5),
                    EvalScore(dimension="Clarity", score=5),
                    EvalScore(dimension="Helpfulness", score=5),
                    EvalScore(dimension="Safety", score=5),
                ],
                total=25,
                passed=True,
                suggestion="",
            )
            # 兜底：全 5 分 + passed=True，保证评测异常时不阻断主流程
            # 这是"中间件出错不影响主流程"的既有约定（与 LoggingMiddleware 一致）

        # 3. 解析裁判输出的 JSON
        data = self._extract_json(text)
        # data 现在是字典（可能为空）

        # 4. 用 pydantic 校验并生成 EvalReport
        try:
            report = EvalReport.model_validate(data)
            # model_validate：按 EvalReport 的规则校验 data
            # 若裁判给的字段/数值不符合 schema（如维度名错、分数越界），会抛异常
        except Exception as e:
            # 裁判 JSON 格式不对，打印后给一个"通过"的保险报告
            print(f"[反思评估] 裁判输出格式解析失败: {e}\n原始输出: {text[:300]}")
            return EvalReport(
                scores=[
                    EvalScore(dimension="Accuracy", score=5),
                    EvalScore(dimension="Completeness", score=5),
                    EvalScore(dimension="Clarity", score=5),
                    EvalScore(dimension="Helpfulness", score=5),
                    EvalScore(dimension="Safety", score=5),
                ],
                total=25,
                passed=True,
                suggestion="",
            )
            # 同样是兜底"通过"，不让格式异常中断主流程

        # 5. 用 should_pass() 再校准一次 passed 字段
        report.passed = report.should_pass()
        # should_pass() 是第 1 步 EvalReport 里的方法：
        #   任一维度 <3 或 总分 <20 → False
        # 用它重算 passed，防止裁判自己填的 passed 与规则不一致

        # 6. 打印评估报告（命令行可视化）
        print(f"\n{'='*60}")
        print(f"[反思评估] {agent_name} 的本次回答")
        print(report.format_summary())
        # format_summary() 输出"总分 + 每维度一行"
        print(f"  是否达标: {'通过' if report.passed else '打回'}")
        print(f"  改进建议: {report.suggestion if report.suggestion else '（无）'}")
        print(f"{'='*60}\n")

        return report
        # 返回结构化报告

    # ===== 同步版本 =====

    def after_agent(self, agent_name: str, result: str, **kwargs) -> dict:
        """
        【同步】Agent 结束后调用（评估入口）
        BaseAgent.run() 在 _run 业务跑完后调用这里

        参数:
            agent_name: Agent 名称
            result:     Agent 的回答
            **kwargs:   含第 2 步加入的 user_input

        返回:
            dict: {"eval_report": EvalReport}，存入中间件 context
        """

        user_input = kwargs.get("user_input", "")
        # 取出第 2步透传进来的用户原始问题（取不到就空字符串，兜底）

        report = self._judge(agent_name, user_input, result)
        # 调用底层裁判逻辑，拿到结构化报告

        return {"eval_report": report}
        # 把报告放进返回 dict，MiddlewareManager 会合并进 after 的 context

    # ===== 异步版本 =====

    async def after_agent_async(self, agent_name: str, result: str, **kwargs) -> dict:
        """
        【异步】Agent 结束后调用（评估入口）
        BaseAgent.arun() 异步版跑完后调用这里

        参数与返回：和同步版一致
        这里因为 _judge 内部用的是 llm.invoke（同步），
        通过 asyncio.to_thread 塞进线程池，避免阻塞事件循环
        """

        user_input = kwargs.get("user_input", "")
        # 同上：取用户原始问题

        import asyncio
        # asyncio：异步编程模块（局部导入，避免文件顶部重复引入）

        report = await asyncio.to_thread(self._judge, agent_name, user_input, result)
        # asyncio.to_thread(同步函数, 参数...)：把同步的 _judge 扔进线程池执行
        # 返回可 await 对象；这样裁判模型的同步 LLM 调用不会卡住异步事件循环
        # 最终拿到 EvalReport

        return {"eval_report": report}
        # 返回报告，存进异步版 after context

# ========== 文件结束 ==========