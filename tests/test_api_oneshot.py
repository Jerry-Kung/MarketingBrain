"""V0.6 oneshot 模式：一次性 LLM 报告流水线的 API 路由集成测试（同步）。"""
import os, sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient


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
        return [CommentRecord(comment_id="c1", video_title="坦克300", job_id="j1", content="评论1"),
                CommentRecord(comment_id="c2", video_title="坦克300", job_id="j1", content="评论2")]
    def data_overview(self):
        from app.datasource.adapter import DataOverview
        return DataOverview(job_count=100, comment_count=100, start_time=None, end_time=None)


class MockLLM:
    def chat_json(self, messages, **kw):
        from app.llm.provider import LLMUsage, LLMResult
        payload = {"scope": {"comment_count": 100}, "themes": [{"theme": "油耗"}]}
        usage = LLMUsage(model="m", prompt_tokens=10, completion_tokens=5,
                         total_tokens=15, latency_ms=3)
        return payload, LLMResult(json.dumps(payload), usage=usage)


def _settings(**kw):
    from app.core.config import Settings
    base = dict(DB_HOST="h", DB_PORT=3306, DB_USER="u", DB_PASSWORD="p", DB_NAME="d",
                LLM_API_BASE="https://x", LLM_API_KEY="k", LLM_MODEL="m",
                ENABLE_WORKFLOW_ENGINE=True, ENABLE_AGENT_ENGINE=True)
    base.update(kw)
    return Settings(**base)


def test_create_task_with_mode_oneshot(tmp_path):
    from app.api.routes import create_app
    app = create_app(
        db_path=os.path.join(tmp_path, "app_state.db"),
        datasource=FakeData(), llm_provider=MockLLM(),
        settings_override=_settings(), background=True, require_sync=True,
    )
    client = TestClient(app)
    resp = client.post("/api/tasks", json={"raw_input": "分析坦克300", "mode": "oneshot"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["mode"] == "oneshot"
    result = data.get("result") or {}
    assert result.get("mode") == "oneshot"
    assert result.get("report", {}).get("scope", {}).get("comment_count") == 100


def test_run_oneshot_sync_no_escape_on_task_missing(tmp_path):
    """任务不存在时返回 {"error": "task not found"}，不抛异常、不调用 mark_failed。

    回归覆盖 review finding：run_oneshot_sync 此前在 task is None 时会继续执行
    LogicalSnapshot.from_dict(task.snapshot)，触发 AttributeError 并被 except
    捕获后错误地调用 task_repo.mark_failed("nope", ...)（对不存在的 task_id
    写入一条 failed 记录）。现应在快照重建前直接短路返回。
    """
    from app.store.repository import TaskRepository, EventRepository
    from app.pipeline.oneshot import run_oneshot_sync

    db = os.path.join(tmp_path, "s.db")
    tr = TaskRepository(db_path=db); tr.init_schema()
    er = EventRepository(db_path=db); er.init_schema()

    result = run_oneshot_sync(
        "nope", task_repo=tr, event_repo=er,
        datasource=FakeData(), llm_provider=MockLLM(), settings=None,
    )

    assert result == {"error": "task not found"}
    # mark_failed 未被调用：不存在的 task_id 不应在 tasks 表留下任何记录
    assert tr.get_task("nope") is None
    # 未追加任何事件（task_started/task_failed 均未触发）
    assert er.get_events("nope") == []
