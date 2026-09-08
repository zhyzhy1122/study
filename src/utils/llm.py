# -*- coding: utf-8 -*-
"""
src/utils/llm.py
作用：模型工厂，封装"创建大模型实例"的逻辑
用法：别的文件 from src.utils.llm import get_deepseek_llm, get_qwen_vl_llm
为什么要封装：
  - 后面有很多 Agent，每个都要用模型
  - 不用每个 Agent 都写一遍创建代码
  - 以后换模型/改参数，只改这一个文件
"""

# ========== 导入部分 ==========

from langchain_openai import ChatOpenAI
# 从 langchain_openai 导入 ChatOpenAI 类
# 作用：LangChain 提供的 OpenAI 格式对话模型封装
# 为什么用它？因为 DeepSeek、通义千问等国内模型都兼容 OpenAI 的 API 格式
# 所以我们可以用同一个类，只改 api_key / base_url / model 就能切换不同模型
# 这样统一接口，业务代码不用关心底层是哪家的模型

from src.config import settings
# 从 config 模块导入 settings 全局单例
# 作用：读取模型配置（api_key、base_url、model 名等）
# 不用在每个文件里自己读 .env


# ========== 函数 1：DeepSeek 文本模型 ==========

def get_deepseek_llm(temperature: float = None) -> ChatOpenAI:
    """
    创建 DeepSeek 文本对话模型实例

    参数:
        temperature: 温度参数（可选），不传就用 settings 里的默认值
                     0 = 最确定（适合代码审查、事实问答）
                     1 = 最随机（适合创意写作、头脑风暴）

    返回:
        ChatOpenAI 实例，可以直接调 .invoke() 或传给 create_agent

    使用示例:
        llm = get_deepseek_llm(temperature=0.3)
        result = llm.invoke("你好")
        print(result.content)
    """

    # 如果调用者没传 temperature，就用 settings 里的默认值
    # settings.deepseek_temperature 是 config.py 里定义的，默认 0.7
    if temperature is None:
        temperature = settings.deepseek_temperature
    # 为什么这么设计？
    # 1. 全局有个默认温度（settings 里），统一控制
    # 2. 特殊场景（比如代码审查要更严谨）可以单独传更低的温度
    # 3. 既统一又灵活

    # 创建 ChatOpenAI 实例
    llm = ChatOpenAI(
        # api_key：API 密钥，从 settings 读
        api_key=settings.deepseek_api_key,

        # base_url：API 地址，DeepSeek 的地址
        base_url=settings.deepseek_base_url,

        # model：使用的模型名称
        model=settings.deepseek_model,

        # temperature：温度参数，控制输出随机性
        temperature=temperature,

        # max_tokens：单次回答最大输出 token 数
        # 设大一点防止长回答中途截断（如学习路线）
        max_tokens=settings.deepseek_max_tokens,
    )
    # ChatOpenAI 是 LangChain 对 OpenAI 格式模型的统一封装
    # 只要模型兼容 OpenAI 格式（DeepSeek、通义、智谱都兼容），就能用
    # 它提供了统一的接口：.invoke()、.stream()、.bind_tools() 等
    # 这样我们写的 Agent 代码不用关心底层是哪家模型

    return llm
    # 返回创建好的模型实例
    # 调用方拿到后直接用就行


# ========== 函数 2：通义千问多模态模型 ==========

def get_qwen_vl_llm(temperature: float = 0.3) -> ChatOpenAI:
    """
    创建通义千问多模态（视觉）模型实例
    用于理解图片（比如用户上传代码报错截图）

    参数:
        temperature: 温度参数，默认 0.3（图片理解要准确，所以温度低一点）

    返回:
        ChatOpenAI 实例（多模态版本，可以接收图片输入）

    注意:
        - 多模态模型只有在需要理解图片时才用
        - 纯文本任务用 get_deepseek_llm() 更便宜、更快
        - 如果没配置 dashscope_api_key，调用时会报错
    """

    # 创建 ChatOpenAI 实例
    llm = ChatOpenAI(
        # api_key：阿里云 DashScope 的 API Key
        # 通义千问是阿里云的产品，通过 DashScope 平台调用
        api_key=settings.dashscope_api_key,

        # base_url：DashScope 的兼容模式地址
        # compatible-mode/v1 表示兼容 OpenAI 的 v1 API 格式
        # 所以我们还是能用 ChatOpenAI 来调用
        base_url=settings.dashscope_base_url,

        # model：多模态模型名称
        model=settings.qwen_vl_model,

        # temperature：温度低一点，图片理解要准确
        temperature=temperature,
    )
    # 虽然是多模态模型，但创建方式和文本模型几乎一样
    # 区别只在于：
    # 1. api_key 和 base_url 不同（阿里云的）
    # 2. model 名不同（多模态模型）
    # 3. 调用时传入的消息里可以包含图片（这个是调用方的事）

    return llm
    # 返回多模态模型实例
    # 调用方可以用它来理解图片


# ========== 关于"为什么两个函数不合并成一个？" ==========
#
# 可能你会想：两个函数长得几乎一样，为什么不写一个通用函数？
#
# 原因：
# 1. 语义清晰：get_deepseek_llm() 一看就知道是文本模型，get_qwen_vl_llm() 一看就知道是多模态
# 2. 默认值不同：文本默认 0.7，多模态默认 0.3
# 3. 以后可能加不同的特有配置（比如多模态有图片尺寸限制，文本有 token 限制）
# 4. 代码可读性 > 代码复用（这是两个不同职责的模型，分开更清楚）
#
# 如果以后模型多了（5个以上），再考虑合并成工厂模式不迟


# ========== 文件结束 ==========
