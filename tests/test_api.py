# -*- coding: utf-8 -*-
"""
tests/test_api.py
FastAPI 应用层冒烟测试（src/api/app.py + routes/health、workspace、export）

覆盖重点：
  - 应用装配：根路由、路由表完整、CORS 中间件、/static 挂载
  - lifespan 启动/关闭流程可正常跑通（MCP 预加载已打桩成 no-op，不联网）
  - workspace 接口：读取默认值、写入校验（空路径 / 不存在的目录）、持久化
  - 导出接口：参数校验 + 真实写文件（落在 tmp_path，不碰 data/exports）
  - 请求体校验：ChatRequest 的空消息被 422 拒绝

注意：/api/chat 与 /api/chat/stream 会真的调用总控与 LLM，本文件不触发它们，
只验证它们的请求模型校验（这一层在进入业务逻辑之前）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import src.api.routes.chat as chat_mod
import src.api.routes.workspace as workspace_mod
from src.api.app import app


# ===========================================================================
# 1. 应用装配
# ===========================================================================

def test_app_metadata_and_docs_routes(api_client_no_lifespan):
    client = api_client_no_lifespan

    schema = client.get("/openapi.json").json()

    assert schema["info"]["title"] == "AI 学习助手 API"
    assert schema["info"]["version"] == "0.1.0"
    assert client.get("/docs").status_code == 200


def test_app_注册了关键路由():
    paths = set(app.openapi()["paths"].keys())

    assert {
        "/",
        "/api/health",
        "/api/chat",
        "/api/chat/stream",
        "/api/workspace",
        "/api/workspace/choose",
        "/api/export",
        "/api/conversations",
        "/api/upload/image",
    } <= paths


def test_root_health_check(api_client_no_lifespan):
    response = api_client_no_lifespan.get("/")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "message": "AI 学习助手 API 运行中",
        "docs": "/docs",
    }


def test_api_health_check(api_client_no_lifespan):
    response = api_client_no_lifespan.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "message": "路由正常工作"}


def test_cors_allows_cross_origin_requests(api_client_no_lifespan):
    """allow_origins=["*"] + allow_credentials=True 时，Starlette 会回显请求的 Origin。"""
    response = api_client_no_lifespan.get("/", headers={"Origin": "http://localhost:5173"})

    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert response.headers["access-control-allow-credentials"] == "true"
    # 简单请求不回 allow-methods，那是预检（OPTIONS）才有的头


def test_cors_preflight_is_answered(api_client_no_lifespan):
    response = api_client_no_lifespan.options(
        "/api/chat",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 200
    assert "POST" in response.headers["access-control-allow-methods"]


def test_no_cache_middleware_only_touches_static_paths(api_client_no_lifespan):
    # 非 /static 路径不应被塞 no-cache 头
    api_response = api_client_no_lifespan.get("/api/health")
    assert "cache-control" not in {k.lower() for k in api_response.headers}

    # /static 路径即使 404 也应带上禁用缓存头，防止前端拿到旧代码
    static_response = api_client_no_lifespan.get("/static/不存在的文件.js")
    assert static_response.status_code == 404
    assert static_response.headers["cache-control"] == "no-cache, no-store, must-revalidate"


def test_static_files_mount_serves_frontend_index():
    client = TestClient(app)
    response = client.get("/static/index.html")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


# ===========================================================================
# 2. lifespan：启动 / 关闭流程
# ===========================================================================

def test_lifespan_starts_and_shuts_down_without_leaking():
    """进入上下文触发启动流程（init_tools + 被打桩的 MCP 预加载），退出触发清理。"""
    from src.modules.tools.registry import get_tool_registry

    # 进入 TestClient 上下文之前，注册中心还是空的
    assert get_tool_registry().get_all_tools() == []

    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        # 启动完成后，子 Agent 工具应已在注册中心里
        assert get_tool_registry().get_tools("sub_agents")

    # 上下文退出后服务仍可再次启动（清理逻辑没有破坏全局状态）
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200


# ===========================================================================
# 3. workspace 接口
# ===========================================================================

def test_get_workspace_falls_back_to_default_export_dir(api_client_no_lifespan, isolated_project_paths):
    payload = api_client_no_lifespan.get("/api/workspace").json()

    default_dir = isolated_project_paths / "exports"
    assert Path(payload["current"]) == default_dir
    assert Path(payload["default_dir"]) == default_dir
    # 未设置过时顺手落盘，保证下次读取稳定
    assert (isolated_project_paths / "workspace.json").exists()
    assert [p["label"] for p in payload["presets"]][0] == "项目 data/exports"


def test_put_workspace_rejects_empty_path(api_client_no_lifespan):
    response = api_client_no_lifespan.put("/api/workspace", json={"path": "   "})

    assert response.status_code == 200
    assert response.json() == {"error": "路径不能为空"}


def test_put_workspace_rejects_missing_directory(api_client_no_lifespan, tmp_path):
    missing = tmp_path / "并不存在的目录"

    response = api_client_no_lifespan.put("/api/workspace", json={"path": str(missing)})

    assert "目录不存在或不可访问" in response.json()["error"]
    assert not missing.exists()


def test_put_workspace_persists_and_get_returns_it(api_client_no_lifespan, tmp_path):
    target = tmp_path / "我的笔记"
    target.mkdir()

    put = api_client_no_lifespan.put("/api/workspace", json={"path": str(target)})
    assert put.json() == {"ok": True, "current": str(target)}

    # 内存缓存生效
    assert Path(api_client_no_lifespan.get("/api/workspace").json()["current"]) == target

    # 持久化文件也写对了（清掉内存缓存后仍能读回来）
    workspace_mod._current = None
    assert Path(api_client_no_lifespan.get("/api/workspace").json()["current"]) == target


def test_workspace_file_is_written_under_tmp_not_project_data(
    api_client_no_lifespan, isolated_project_paths
):
    """回归保护：这一层必须在 tmp_path 下，绝不能污染项目的 data/workspace.json。"""
    api_client_no_lifespan.get("/api/workspace")

    assert workspace_mod._WORKSPACE_FILE == isolated_project_paths / "workspace.json"
    assert workspace_mod._WORKSPACE_FILE.exists()
    # 注入进来的路径常量指向 tmp，说明测试环境的隔离真的生效
    assert "学习之家" not in str(workspace_mod._WORKSPACE_FILE)


# ===========================================================================
# 4. 导出接口（真实写盘，但落在 tmp）
# ===========================================================================

def test_export_returns_error_for_empty_content(api_client_no_lifespan):
    response = api_client_no_lifespan.post(
        "/api/export", json={"content": "", "filename": "路线", "format": "md"}
    )

    assert response.json() == {"error": "内容为空"}


def test_export_rejects_unsupported_format(api_client_no_lifespan):
    response = api_client_no_lifespan.post(
        "/api/export", json={"content": "# 路线", "filename": "路线", "format": "pdf"}
    )

    assert "不支持的格式" in response.json()["error"]


def test_export_writes_markdown_into_current_workspace(api_client_no_lifespan, tmp_path):
    target = tmp_path / "导出目录"
    target.mkdir()
    api_client_no_lifespan.put("/api/workspace", json={"path": str(target)})

    content = "# 📚 学习路线：Python\n\n## 阶段一：入门\n- 变量与类型\n"
    response = api_client_no_lifespan.post(
        "/api/export",
        json={"content": content, "filename": "Python学习路线", "format": "md"},
    )
    payload = response.json()

    assert payload["filename"] == "Python学习路线.md"
    assert payload["format"] == "md"
    written = Path(payload["path"])
    assert written == target / "Python学习路线.md"
    assert written.read_text(encoding="utf-8") == content


def test_export_sanitizes_illegal_filename_characters(api_client_no_lifespan, tmp_path):
    response = api_client_no_lifespan.post(
        "/api/export",
        json={"content": "正文", "filename": 'a/b:c*d?e"f', "format": "md"},
    )

    assert response.json()["filename"] == "abcdef.md"


def test_export_docx_produces_a_real_docx(api_client_no_lifespan, tmp_path):
    target = tmp_path / "docx目录"
    target.mkdir()
    api_client_no_lifespan.put("/api/workspace", json={"path": str(target)})

    md = "# 学习路线\n\n## 阶段一\n\n- 知识点 A\n- 知识点 B\n\n段落文字\n"
    response = api_client_no_lifespan.post(
        "/api/export", json={"content": md, "filename": "路线", "format": "docx"}
    )
    payload = response.json()

    written = Path(payload["path"])
    assert payload["format"] == "docx"
    assert written.exists()
    assert written.suffix == ".docx"
    assert written.read_bytes()[:2] == b"PK"  # docx 是 zip 容器
    assert written.stat().st_size > 0


# ===========================================================================
# 5. 请求体校验（不进入业务逻辑，因此不会调用 LLM）
# ===========================================================================

@pytest.mark.parametrize("message", ["", "x" * 5001])
def test_chat_request_body_validation(api_client_no_lifespan, message):
    response = api_client_no_lifespan.post("/api/chat", json={"message": message})

    assert response.status_code == 422  # min_length=1 / max_length=5000


def test_chat_request_rejects_missing_message(api_client_no_lifespan):
    response = api_client_no_lifespan.post("/api/chat", json={})

    assert response.status_code == 422


def test_chat_module_paths_are_isolated(api_client_no_lifespan, isolated_project_paths):
    """回归保护：chat 路由的日志/上传/会话库路径必须都指向 tmp。"""
    assert chat_mod._LOG_FILE == isolated_project_paths / "debug_chat.log"
    assert chat_mod._CONV_DB_PATH == isolated_project_paths / "conversations.db"
    assert chat_mod._UPLOAD_DIR == isolated_project_paths / "uploads"
    for path in (chat_mod._LOG_FILE, chat_mod._CONV_DB_PATH, chat_mod._UPLOAD_DIR):
        assert "学习之家" not in str(path)
