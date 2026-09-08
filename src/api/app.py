# -*- coding: utf-8 -*-
"""
src/api/app.py
FastAPI 应用入口

作用：
  1. 创建 FastAPI 应用实例
  2. 配置 lifespan（启动时初始化工具）
  3. 注册路由（chat 等）
  4. 配置 CORS（允许前端跨域访问）
  5. 挂载静态文件（前端页面）

为什么单独一个文件：
  这是整个 Web 后端的"总装车间"——把所有零件拼起来。
  uvicorn 启动时直接 import 这个文件里的 app 实例。
"""

# ========== 导入 ==========

from fastapi import FastAPI
# FastAPI：应用类，整个 Web 服务的根

from fastapi.middleware.cors import CORSMiddleware
# CORS 中间件：允许浏览器从不同域名访问 API
# 开发时前端跑在 5173（Vite）或直接打开文件，后端在 8000，必须开 CORS

from fastapi.staticfiles import StaticFiles
# StaticFiles：静态文件服务（HTML/CSS/JS、上传的图片等）

from pathlib import Path
# Path：路径操作，定位前端静态文件目录

from src.api.deps import lifespan
# lifespan：应用生命周期（启动初始化、关闭清理）

from src.api.routes.chat import router as chat_router
# chat 路由：/api/chat 接口
# 重命名为 chat_router，避免和将来其他 router 重名

from src.api.routes.workspace import router as workspace_router
# workspace 路由：/api/workspace 接口（前端选择导出目标文件夹）
# 包含三个接口：GET/PUT 读写工作空间、POST /choose 弹文件夹选择框

# ========== 路径配置 ==========

BASE_DIR = Path(__file__).resolve().parent.parent.parent
# 项目根目录：E:\学习之家
# __file__ = src/api/app.py
# .parent = src/api
# .parent.parent = src
# .parent.parent.parent = 项目根目录

FRONTEND_DIR = BASE_DIR / "frontend"
# 前端静态文件目录：项目根/frontend


UPLOAD_DIR = BASE_DIR / "data" / "uploads"
# 上传文件目录：data/uploads
# P4 做图片上传时会用到，

# ========== 确保目录存在 ==========

FRONTEND_DIR.mkdir(exist_ok=True)
# 确保 frontend 目录存在，不存在就创建
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
# 确保 uploads 目录存在，parents=True 表示父目录不存在也一起建

# ========== 创建 FastAPI 应用 ==========

app = FastAPI(
    title="AI 学习助手 API",
    # API 标题（在 Swagger 文档里显示）
    description="基于多 Agent 架构的 AI 学习助手",
    # API 描述
    version="0.1.0",
    # 版本号
    lifespan=lifespan,
    # 生命周期：启动时初始化工具，关闭时清理
)

# ========== 配置 CORS ==========

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    # 允许所有来源（开发阶段方便，生产环境要收紧）
    allow_credentials=True,
    # 允许携带 cookie
    allow_methods=["*"],
    # 允许所有 HTTP 方法（GET/POST/PUT/DELETE 等）
    allow_headers=["*"],
    # 允许所有请求头
)
# CORS 配置加在最前面，所有请求都会经过这个中间件

# ========== 注册路由 ==========

app.include_router(chat_router)
# 把 chat 路由挂到应用上
# chat_router 自己已经带了 /api prefix，所以最终路径是 /api/chat

app.include_router(workspace_router)
# 把 workspace 路由挂到应用上
# workspace_router 自己也带了 /api 前缀，最终路径是 /api/workspace*

# ========== 挂载静态文件 ==========

# 禁用缓存：开发阶段必须实时拿到最新前端代码
from fastapi.responses import Response

@app.middleware("http")
async def no_cache_middleware(request, call_next):
    """
    开发用中间件：所有响应都加 no-cache 头
    否则浏览器会用 304 缓存旧版前端代码，前端改动不生效
    """
    response = await call_next(request)
    if request.url.path.startswith("/static"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")
# 把 frontend 目录挂载到 /static 路径
# 访问 http://localhost:8000/static/index.html 就能拿到前端页面
# 先挂上，P2 做前端页面时直接用

# ========== 根路径健康检查 ==========

@app.get("/")
async def root():
    """
    根路径：健康检查
    访问 http://localhost:8000/ 看服务是不是活的
    """
    return {
        "status": "ok",
        "message": "AI 学习助手 API 运行中",
        "docs": "/docs",
        # 提示去 /docs 看 Swagger 文档
    }
