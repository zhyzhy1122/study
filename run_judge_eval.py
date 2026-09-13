# -*- coding: utf-8 -*-
"""
「学习之家」回答质量评测 · 单文件版（评测集已内置）
用法：把本文件放到 E:\学习之家 项目根目录（和 run_api.py 平级），然后：
    python run_judge_eval.py
前提：项目能正常启动（.env 里 DEEPSEEK_API_KEY 有效）
注意：路线规划类问题一次要 60-90 秒，整轮跑完约 5-10 分钟。
"""
import json
import time
from openai import OpenAI

# ===== 项目真实配置与入口 =====
from src.config import settings                      # 读 .env 里的 deepseek_api_key
from src.agents.supervisor import run_supervisor     # 同步调用总控 Agent

_judge = OpenAI(api_key=settings.deepseek_api_key, base_url=settings.deepseek_base_url)

# ===== 评测集：6 条，覆盖 学习路线 / 代码审查 / 知识问答 / 寒暄 四类场景 =====
EVAL_SET = [
    {"scene": "代码审查", "question": "帮我审查这段 Python 代码：\nfor i in range(len(lst)):\n    print(lst[i])\n指出问题并给出改进写法。"},
    {"scene": "代码审查", "question": "审查这个函数有什么问题：\ndef add(a, b):\n    return a + b\n从类型安全、异常处理、文档字符串三个角度分析。"},
    {"scene": "代码审查", "question": "这段代码有性能问题吗？\ns = \"\"\nfor x in big_list:\n    s += str(x)\n如果有，给出更好的写法并解释原因。"},
    {"scene": "知识问答", "question": "不用联网搜索，凭你已有的知识回答：什么是 RAG？为什么企业客服系统普遍用它？"},
    {"scene": "知识问答", "question": "不用联网搜索，用通俗的语言解释 FastAPI 的依赖注入是什么，并举一个例子。"},
    {"scene": "寒暄", "question": "你好，你能帮我做什么？"},
]


DIMENSIONS = ["relevance", "accuracy", "completeness", "actionability", "format"]


def judge(question: str, answer: str) -> dict:
    """让 LLM 按 5 维度打分（25 分制）并判定问题是否被解决"""
    prompt = f"""你是严格的评测员。根据【用户问题】和【AI回答】，按以下 5 个维度各打 1-5 分：
1. relevance 相关性：回答是否针对问题本身
2. accuracy 准确性：事实和技术点是否可信、无编造
3. completeness 完整性：问题要点是否都覆盖
4. actionability 可操作性：用户拿到回答能否直接行动
5. format 格式规范：表达清晰、结构合理

同时判定 problem_solved：回答是否正确解决了问题（true/false）。

【用户问题】{question}
【AI回答】{answer}

只输出 JSON：{{"relevance":x,"accuracy":x,"completeness":x,"actionability":x,"format":x,"problem_solved":true,"reason":"一句话"}}"""
    resp = _judge.chat.completions.create(
        model=settings.deepseek_model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        response_format={"type": "json_object"},
    )
    return json.loads(resp.choices[0].message.content)


def main():
    total, solved, scores = 0, 0, []
    details = []
    print(f"===== 「学习之家」回答质量评测（{len(EVAL_SET)} 条，约 5-10 分钟）=====\n")
    for c in EVAL_SET:
        t0 = time.time()
        try:
            answer = run_supervisor(c["question"])
        except Exception as e:
            answer = f"[调用失败: {e}]"
        latency = time.time() - t0

        try:
            j = judge(c["question"], answer)
        except Exception as e:
            print(f"[{c['scene']}] 裁判调用失败: {e}")
            continue

        score = sum(j[k] for k in DIMENSIONS)
        total += 1
        solved += bool(j["problem_solved"])
        scores.append(score)
        details.append({"scene": c["scene"], "question": c["question"], "score": score,
                        "solved": j["problem_solved"], "latency_s": round(latency, 1),
                        "reason": j.get("reason", "")})
        print(f"[{c['scene']}] {c['question'][:16]}... → {score}/25  solved={j['problem_solved']}  {latency:.1f}s")
        print(f"    裁判意见: {j.get('reason', '')}")

    if total:
        avg = sum(scores) / total
        rate = solved / total * 100
        print(f"\n解决率: {solved}/{total} = {rate:.0f}%   平均质量分: {avg:.1f}/25")
        json.dump({"solved": solved, "total": total, "solve_rate": round(rate, 1),
                   "avg_score": round(avg, 1), "details": details},
                  open("judge_eval_result.json", "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        print(">>> 结果已存档到 judge_eval_result.json")


if __name__ == "__main__":
    main()
