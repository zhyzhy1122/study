# -*- coding: utf-8 -*-
"""
src/modules/multimodal/qwen_vl.py
作用：Qwen-VL 多模态模型调用封装

功能：
  - 输入：图片（本地路径 / URL / base64）+ 文本问题
  - 输出：模型的文字回答

为什么单独一个文件：
  - 多模态调用签名和纯文本 LLM 不一样（要处理图片）
  - 以后换视觉模型（GPT-4o / Claude 3 / Gemini）只改这个文件
  - 保持 utils/llm.py 的纯文本定位

依赖：
  - dashscope（阿里云百炼 SDK）
  - 配置：settings.dashscope_api_key
"""

# ========== 导入部分 ==========

import base64
# base64：把本地图片文件编码成 base64 字符串
# Qwen-VL 支持 base64 格式的图片输入

from pathlib import Path
# Path：路径处理

from typing import Optional
# Optional：可选类型

from src.config import settings
# 全局配置（dashscope_api_key）

import os
# DashScope 是阿里云国内服务，走系统代理（Clash 等）常导致 SSL 连接被中断
# （症状：SSLError UNEXPECTED_EOF_WHILE_READING，直连正常、走代理必挂）。
# 这里强制 dashscope 域名绕过代理直连，保证多模态链路不受本机代理软件影响。
_no_proxy = os.environ.get("NO_PROXY", "")
if "dashscope.aliyuncs.com" not in _no_proxy:
    _merged = f"{_no_proxy},dashscope.aliyuncs.com".lstrip(",")
    os.environ["NO_PROXY"] = _merged
    os.environ["no_proxy"] = _merged

# ========== 常量 ==========

QWEN_VL_MODEL = "qwen-vl-max"
# Qwen-VL 的模型名
# qwen-vl-max：视觉理解能力最强的版本（你用这个就好）
# 还有 qwen-vl-plus（性价比版）、qwen-vl-chat（基础版）
# 参考：https://help.aliyun.com/zh/model-studio/developer-reference/use-qwen-vl

# ========== 图片编码工具 ==========

def _image_to_base64(image_path: str) -> str:
    """
    把本地图片文件转成 base64 字符串（带 data URL 前缀）

    参数:
        image_path: 本地图片文件路径

    返回:
        str：带 data:image/xxx;base64, 前缀的完整 base64 字符串
        如 "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg..."

    为什么需要前缀：
      Qwen-VL 的 image 字段要区分"URL"和"base64 图片"。
      不带前缀的纯 base64 会被当成 URL 解析，报 URL 格式错误。
      加了 data:image/xxx;base64, 前缀，模型才知道这是 base64 图片。
    """
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"图片文件不存在: {image_path}")

    # 根据文件后缀判断 MIME 类型
    suffix = path.suffix.lower()
    if suffix in ('.png',):
        mime_type = "image/png"
    elif suffix in ('.jpg', '.jpeg'):
        mime_type = "image/jpeg"
    elif suffix in ('.gif',):
        mime_type = "image/gif"
    elif suffix in ('.webp',):
        mime_type = "image/webp"
    else:
        # 未知格式默认 png
        mime_type = "image/png"

    with open(path, "rb") as f:
        img_bytes = f.read()

    b64_str = base64.b64encode(img_bytes).decode("utf-8")
    # 纯 base64 字符串

    return f"data:{mime_type};base64,{b64_str}"
    # 加上 data URL 前缀，Qwen-VL 才能识别这是 base64 图片

# ========== 核心调用函数 ==========

def call_qwen_vl(
    prompt: str,
    image_path: Optional[str] = None,
    image_url: Optional[str] = None,
    image_base64: Optional[str] = None,
    model: str = QWEN_VL_MODEL,
) -> str:
    """
    调用 Qwen-VL 多模态模型

    参数:
        prompt:       文本问题/提示（如"描述这张图"、"看看这个报错怎么解决"）
        image_path:   本地图片路径（三选一）
        image_url:    图片 URL（三选一）
        image_base64: 已编码的 base64 图片（三选一）
        model:        模型名，默认 qwen-vl-max

    返回:
        str：模型的文字回答

    使用方式（三选一即可）：
        call_qwen_vl("描述这张图", image_path="截图.png")
        call_qwen_vl("描述这张图", image_url="https://xxx.com/1.jpg")
        call_qwen_vl("描述这张图", image_base64="iVBORw0KGg...")

    原理：
      Qwen-VL 的消息格式支持 "image" 类型的内容，
      我们把图片和文字一起塞进消息里，模型就会"看图说话"。
    """
    # ---------- 0. 导入 dashscope（延迟导入，避免没装也能 import 本模块） ----------
    import dashscope
    # dashscope：阿里云百炼官方 SDK
    # 放在函数内 import，好处：如果用户没装 dashscope，import 本模块不会直接炸
    # 只有真正调用时才会报错，错误信息更明确

    # ---------- 1. API Key 配置 ----------
    if not settings.dashscope_api_key:
        raise ValueError("dashscope_api_key 未配置，请在 .env 里设置 DASHSCOPE_API_KEY")
        # key 没配就明确报错，别等调 API 才发现

    dashscope.api_key = settings.dashscope_api_key
    # 把 key 设给 SDK

    # ---------- 2. 组装图片内容 ----------
    # 三种输入方式，优先用已给的 base64，其次是 URL，最后是本地路径（需要转 base64）
    if image_base64:
        img_b64 = image_base64
        # 已经是 base64 了，直接用
    elif image_url:
        img_b64 = image_url
        # URL 直接传字符串，Qwen-VL 会自己去拉
        # 注意：这里不做 base64 转换，Qwen-VL SDK 支持 URL 格式
    elif image_path:
        img_b64 = _image_to_base64(image_path)
        # 本地文件 → 转 base64
    else:
        raise ValueError("必须提供 image_path / image_url / image_base64 其中之一")
        # 三个都没给 → 明确报错

    # ---------- 3. 构造消息 ----------
    # Qwen-VL 的消息格式：content 是一个列表，可以混 text 和 image
    messages = [
        {
            "role": "user",
            "content": [
                {"image": img_b64},
                # 图片内容（base64 或 URL）
                {"text": prompt},
                # 文本内容（用户的问题/提示）
            ],
        }
    ]
    # 这个格式是 Qwen-VL 规定的：content 是数组，里面放 image 和 text
    # 模型会先"看"图，再"读"问题，然后回答

    # ---------- 4. 调用模型 ----------
    response = dashscope.MultiModalConversation.call(
        model=model,
        # 模型名
        messages=messages,
        # 消息列表（含图+文）
    )
    # MultiModalConversation.call：多模态对话接口
    # 这是 dashscope SDK 里专门给 Qwen-VL 用的调用方法

    # ---------- 5. 解析返回 ----------
    if response.status_code == 200:
        # 200 = 成功
        answer = response.output.choices[0].message.content
        # 取出回答内容
        # 注意：Qwen-VL 的 content 可能是字符串，也可能是 [{"text":"..."}] 格式
        # 这里做个兼容处理
        if isinstance(answer, list):
            # 如果是列表，找出 text 字段
            for item in answer:
                if isinstance(item, dict) and "text" in item:
                    return item["text"]
            return str(answer)
        return str(answer)
    else:
        # 失败，抛出明确的错误
        raise RuntimeError(
            f"Qwen-VL 调用失败 (status_code={response.status_code}): "
            f"{getattr(response, 'message', str(response))}"
        )

# ========== 文件结束 ==========