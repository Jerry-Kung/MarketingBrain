"""V0.2 API 测试：任务后台执行、报告与证据端点。

用 create_app(background=False) 同步执行 + stub datasource + mock LLM，
避免真实网络与轮询。
"""
import os
import sys
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
import pytest


class FakeData:
    def count_comments(self, **kw): return 100
    def time_series(self, **kw):
        return {"buckets": [{"start": "2026-08-01", "count": 100}], "total": 100}
    def top_videos(self, **kw):
        return [{"job_id": "j1", "video_title": "坦克300", "comment_count": 50, "like_sum": 10}]
    def topic_frequency(self, **kw):
        return [{"topic": "油耗", "comment_count": 40}]
    def fetch_comments(self, limit=30, **kw):
        from app.datasource.models import CommentRecord
        return [CommentRecord(comment_id="c1", video_title="坦克300", job_id="j1",
                              content="评论1"),
                CommentRecord(comment_id="c2", video_title="坦克300", job_id="j1",
                              content="评论2")]
    def data_overview(self):
        from app.datasource.adapter import DataOverview
        return DataOverview(job_count=100, comment_count=100, start_time=None, end_time=None)


class MockLLM:
    def chat_json(self, messages, **kw):
        import json
        from app.llm.provider import LLMUsage, LLMResult
        payload = {"scope": {"comment_count": 100},
                   "themes": [{"theme": "油耗", "refs": ["c1"]}]}
        usage = LLMUsage(model="m", prompt_tokens=10, completion_tokens=5,
                         total_tokens=15, latency_ms=3)
        return payload, LLMResult(json.dumps(payload), usage=usage)


@pytest.fixture
def client(tmp_path):
    from app.api.routes import create_app
    db_path = os.path.join(tmp_path, "app_state.db")
    app = create_app(
        db_path=db_path, datasource=FakeData(), llm_provider=MockLLM(),
        background=False,  # 同步执行，便于测试
        settings=None,
    )
    return TestClient(app)


class TestV02:
    def test_create_task_runs_to_success(self, client):
        resp = client.post("/api/tasks", json={"raw_input": "分析坦克300近期的舆情变化"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"  # background=False 同步执行
        assert body["result"]["report"]["scope"]["comment_count"] == 100

    def test_report_endpoint(self, client):
        resp = client.post("/api/tasks", json={"raw_input": "分析坦克300近期"})
        task_id = resp.json()["task_id"]
        r2 = client.get(f"/api/tasks/{task_id}/report")
        assert r2.status_code == 200
        assert r2.json()["report"]["scope"]["comment_count"] == 100

    def test_evidence_endpoint(self, client):
        resp = client.post("/api/tasks", json={"raw_input": "分析坦克300近期"})
        task_id = resp.json()["task_id"]
        r2 = client.get(f"/api/tasks/{task_id}/evidence")
        assert r2.status_code == 200
        evs = r2.json()["evidence"]
        assert any(e["comment_id"] == "c1" for e in evs)

    def test_nonexistent_report_404(self, client):
        assert client.get("/api/tasks/none/report").status_code == 404

    def test_default_window_when_no_time_phrase(self, client):
        """Controller 裁定 P5：无时间短语时默认取最近 30 天窗口。"""
        resp = client.post("/api/tasks", json={"raw_input": "最近有什么热点吗"})
        assert resp.status_code == 200
        body = resp.json()
        snap = body["snapshot"]
        assert snap["start_time"] and snap["end_time"]
        start = datetime.fromisoformat(snap["start_time"])
        end = datetime.fromisoformat(snap["end_time"])
        delta = (end - start).days
        assert 29 <= delta <= 31, f"期望约 30 天窗口，实际 {delta} 天"

    def test_llm_unconfigured_marks_task_failed(self, tmp_path):
        """Controller 裁定 P2：LLM 未配置时不运行、不 500，任务标记失败。"""
        from app.api.routes import create_app
        from app.core.config import Settings

        # DB 字段齐全通过 model_post_init 校验；LLM 三者置 None 模拟未配置
        s = Settings(DB_HOST="h", DB_PORT=3306, DB_USER="u",
                     DB_PASSWORD="p", DB_NAME="d",
                     LLM_API_BASE=None, LLM_API_KEY=None, LLM_MODEL=None)
        app = create_app(
            db_path=os.path.join(tmp_path, "app_state.db"),
            datasource=None, settings=s, llm_provider=None, background=False,
        )
        c = TestClient(app)
        resp = c.post("/api/tasks", json={"raw_input": "分析坦克300近期"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "failed"
        assert body["error"] == "LLM 未配置（缺少 LLM_API_BASE/LLM_API_KEY/LLM_MODEL）"
        # result 默认空 dict（TaskRecord.result 默认 {}，非 None），
        # 但仍表示“无结果”，与报告端点 not task.result 判定一致。
        assert not body["result"]
