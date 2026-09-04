"""API 层测试（FastAPI TestClient）。

验证健康检查、任务创建、任务状态、数据概览等端点。API 依赖通过依赖注入
替换真实 DB 连接，保证单测不依赖真实 MySQL。
"""
import os
import sys
from pathlib import Path

import pytest

# 确保应用可导入
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    """构造一个使用临时 SQLite 存储、stub 数据源的 FastAPI 应用。"""
    from app.api.routes import create_app

    # 临时状态目录
    state_dir = tmp_path / "state"
    state_dir.mkdir(exist_ok=True)
    db_path = os.path.join(state_dir, "app_state.db")

    app = create_app(db_path=db_path, datasource=None)  # None=禁用真实 DB（数据概览降级）
    return TestClient(app)


class TestHealth:
    def test_health_ok(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "database" in body


class TestTaskLifecycle:
    def test_create_task_parses_intent(self, client):
        resp = client.post("/api/tasks", json={"raw_input": "分析坦克300近期的舆情变化"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["task_id"]
        # 意图应被解析出来（坦克300 在关键词表里）
        assert body["status"] == "pending"
        assert body["parsed_intent"]["object"] == "坦克300"

    def test_create_task_accepts_empty_object(self, client):
        resp = client.post("/api/tasks", json={"raw_input": "最近有什么热点吗"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["task_id"]

    def test_get_task_status(self, client):
        resp = client.post("/api/tasks", json={"raw_input": "分析坦克300"})
        task_id = resp.json()["task_id"]
        resp2 = client.get(f"/api/tasks?task_id={task_id}")
        assert resp2.status_code == 200
        body = resp2.json()
        assert body["task_id"] == task_id
        assert body["status"] == "pending"

    def test_missing_input_returns_422(self, client):
        resp = client.post("/api/tasks", json={})
        assert resp.status_code == 422


class TestDataOverview:
    def test_data_overview_without_db(self, client):
        """数据源不可用时，概览应返回可用性说明而非 500。"""
        resp = client.get("/api/data-overview")
        # 数据源为 None 时，返回降级信息（200）
        assert resp.status_code == 200
        body = resp.json()
        assert "available" in body


class TestStaticFrontend:
    """前端静态产物由 FastAPI 同源提供：Vite 产物引用根路径 /assets/*、/favicon.svg。"""

    @pytest.fixture
    def static_client(self, tmp_path):
        from app.api.routes import create_app

        static_dir = tmp_path / "static"
        (static_dir / "assets").mkdir(parents=True)
        (static_dir / "index.html").write_text(
            '<script src="/assets/index-abc.js"></script>', encoding="utf-8"
        )
        (static_dir / "assets" / "index-abc.js").write_text("console.log(1)", encoding="utf-8")
        (static_dir / "favicon.svg").write_text("<svg/>", encoding="utf-8")

        app = create_app(
            db_path=os.path.join(tmp_path, "app_state.db"),
            datasource=None,
            static_dir=str(static_dir),
        )
        return TestClient(app)

    def test_index_served(self, static_client):
        resp = static_client.get("/")
        assert resp.status_code == 200
        assert "/assets/index-abc.js" in resp.text

    def test_assets_served_at_root(self, static_client):
        assert static_client.get("/assets/index-abc.js").status_code == 200
        assert static_client.get("/favicon.svg").status_code == 200

    def test_api_still_takes_precedence(self, static_client):
        assert static_client.get("/api/health").status_code == 200
