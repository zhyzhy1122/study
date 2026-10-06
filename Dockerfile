FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 先装依赖单独一层，改代码时依赖缓存可复用
COPY requirements.txt .
# 国内网络如需镜像源：
#   docker compose build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN pip install --no-cache-dir -r requirements.txt -i ${PIP_INDEX_URL}

# 后端代码（src 为命名空间包，CWD=/app 时 import src.api.app 可用）
COPY src ./src
# 前端静态页面（FastAPI 挂载在 /static，无需前端构建步骤）
COPY frontend ./frontend

# 运行时数据目录（数据库与上传文件由数据卷挂载覆盖）
RUN mkdir -p data/uploads data/exports

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/', timeout=3).status==200 else 1)"

# 注意：不要用 run_api.py（里面 reload=True 是开发用法）
CMD ["uvicorn", "src.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
