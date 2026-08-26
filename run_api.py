# -*- coding: utf-8 -*-
"""
run_api.py
FastAPI 服务启动脚本

用法：
  ./.venv/Scripts/python run_api.py
  或：uvicorn src.api.app:app --host 0.0.0.0 --port 8000 --reload

为什么单独建这个文件：
  1. 方便直接点运行（不用记 uvicorn 命令）
  2. 可以在这里加一些环境变量、日志配置
"""

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "src.api.app:app",
        # 应用位置：src.api.app 模块里的 app 变量
        host="0.0.0.0",
        # 监听所有网卡（本机和局域网都能访问）
        port=8000,
        # 端口号
        reload=True,
        # 代码变更自动重启（开发阶段用，生产关掉）
    )