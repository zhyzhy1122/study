# -*- coding: utf-8 -*-
"""
src/config.py
作用：项目的配置中心，统一管理所有配置项（API Key、模型名称、地址等）
用法：别的文件 from src.config import settings 就能拿到所有配置
"""

# ========== 导入部分 ==========

from pydantic_settings import BaseSettings
# 从 pydantic_settings 导入 BaseSettings 基类
# 作用：BaseSettings 是 pydantic 官方提供的配置管理基类
# 继承它的类会自动从环境变量和 .env 文件里读取配置值
# 还会自动做类型检查（比如你定义了 str 类型，传了数字会报错）

from pathlib import Path
# 从 pathlib 导入 Path 类
# 作用：Python 标准库提供的路径处理工具，比直接写字符串路径更安全、更跨平台
# 比如 Path("a/b/c") 在 Windows 上自动变成 "a\b\c"，在 Linux 上是 "a/b/c"


# ========== 配置类 ==========

class Settings(BaseSettings):
    # Settings 类继承自 BaseSettings
    # 类里面的每个属性就是一个配置项
    # BaseSettings 会自动从环境变量 / .env 文件里找对应的值
    # 查找规则：属性名大写 → 找同名环境变量（比如 deepseek_api_key 找 DEEPSEEK_API_KEY）

    # --- DeepSeek 文本模型配置 ---
    deepseek_api_key: str
    # deepseek_api_key：DeepSeek 的 API 密钥
    # 类型是 str（字符串）
    # 没有默认值，说明是必填项——如果 .env 里没配置，启动会直接报错
    # 这样可以防止"忘了配 Key 导致运行时神秘报错"的问题

    deepseek_base_url: str = "https://api.deepseek.com"
    # deepseek_base_url：DeepSeek API 的地址
    # 类型 str，默认值是 "https://api.deepseek.com"
    # 有默认值的配置项就是"可选"的，不配置也能用默认值跑

    deepseek_model: str = "deepseek-v4-flash"
    # deepseek_model：使用的模型名称
    # 默认值 "deepseek-chat" 是 DeepSeek 的主力对话模型
    # 以后想换模型（比如 deepseek-reasoner），改这里就行

    deepseek_temperature: float = 0.7
    # deepseek_temperature：温度参数
    # 类型是 float（浮点数/小数）
    # 温度控制输出的随机性：0=最确定、1=最随机
    # 代码审查建议 0.3 左右（更严谨），创意生成建议 0.7-1.0

    # --- 通义千问多模态模型配置 ---
    dashscope_api_key: str = ""
    # dashscope_api_key：阿里云 DashScope 的 API 密钥
    # （通义千问的 API 是通过阿里云 DashScope 平台调用的）
    # 默认值是空字符串 ""，表示可选配置
    # 因为多模态是后面才用的，现在不配也能跑项目

    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    # dashscope_base_url：DashScope 的 API 地址
    # 用的是 "compatible-mode"（兼容模式），兼容 OpenAI 格式
    # 这样我们可以用 ChatOpenAI 来调用千问，不用单独学新 SDK

    qwen_vl_model: str = "qwen-vl-max"
    # qwen_vl_model：通义千问的多模态模型名
    # qwen-vl-max 是千问当前最强的多模态版本
    # 以后如果出了更新的模型，改这里就行

    # --- Tavily 搜索配置 ---
    tavily_api_key: str = ""
    # tavily_api_key：Tavily 搜索的 API 密钥
    # 默认值是空字符串 ""，表示可选配置
    # 用到搜索功能的时候才需要配置
    
    # --- 项目基本配置 ---
    app_name: str = "学习之家 AI"
    # app_name：项目名称，显示在日志、标题等地方

    debug: bool = False
    # debug：是否开启调试模式
    # 类型是 bool（布尔值：True 或 False）
    # True 的话会打印更多日志，方便排查问题
    # 开发时可以设 True，上线设 False

    @property
    def project_root(self) -> Path:
        # project_root：项目根目录路径（自动计算，不用配置）
        # 用 @property 装饰，表示这是一个"计算属性"
        # 不是从 .env 读的配置项，而是每次访问时动态算出来的
        # __file__ 是当前文件（config.py）的路径
        # .parent.parent 往上两级就是项目根目录
        # .resolve() 转成绝对路径，避免歧义
        return Path(__file__).parent.parent.resolve()
    # 用法：settings.project_root 就能拿到项目根目录的 Path 对象
    # 比如 settings.project_root / "data" 就是 E:\学习之家\data

    # ========== 内部配置：模型加载 ==========

    class Config:
        # Config 是 Settings 类的内部类（嵌套类）
        # 用来配置 BaseSettings 的行为
        # 名字必须叫 Config，这是 pydantic 的约定

        env_file = ".env"
        # env_file：指定从哪个文件读取环境变量
        # 这里写 ".env"，表示从项目根目录下的 .env 文件读
        # pydantic-settings 会自动找到这个文件并加载里面的配置

        env_file_encoding = "utf-8"
        # env_file_encoding：.env 文件的编码格式
        # 设成 utf-8，防止中文乱码


# ========== 全局单例 ==========

settings = Settings()
# 创建 Settings 类的一个实例，命名为 settings
# 这就是项目里唯一的配置实例（单例模式）
# 别的文件用 from src.config import settings 就能拿到所有配置
# 比如 settings.deepseek_api_key 就能拿到 API Key

# 为什么用单例？
# 1. 整个项目只有一份配置，避免到处创建造成不一致
# 2. 导入方便，任何地方一行代码就能拿到
# 3. Python 模块导入天然就是单例（导入多次也只执行一次）


# ========== 文件结束 ==========
# 当别的文件 import 这个模块时，上面的代码会执行一遍
# settings 变量就被创建好了，可以直接使用
