"""任务执行器测试（同步路径，stub datasource + mock LLM）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datetime import datetime
import json
import pytest

from app.pipeline.runner import run_task_sync
from app.store.repository import TaskRepository, EventRepository
from app.snapshot.snapshot import LogicalSnapshot
from app.llm.provider import LLMResult, LLMUsage


class FakeData:
    def __init__(self):
        self.calls = []
    def count_comments(self, **kw):
        self.calls.append(("count", kw)); return 100
    def time_series(self, **kw):
        return {"buckets": [{"start": "2026-08-01", "count": 100}], "total": 100}
    def top_videos(self, **kw):
        return [{"job_id": "j1", "video_title": "坦克300", "comment_count": 50, "like_sum": 10}]
    def topic_frequency(self, **kw):
        return [{"topic": "油耗", "comment_count": 40}]
    def fetch_comments(self, limit=30, **kw):
        from app.datasource.models import CommentRecord
        ids = ["c1", "c2"]
        return [CommentRecord(comment_id=i, video_title="坦克300", job_id="j1",
                              content=f"评论{i}") for i in ids]


class MockLLM:
    def chat_json(self, messages, **kw):
        usage = LLMUsage(model="m", prompt_tokens=10, completion_tokens=5,
                         total_tokens=15, latency_ms=3)
        payload = {"scope": {"comment_count": 100}, "themes": [{"theme": "油耗", "refs": ["c1"]}]}
        return payload, LLMResult(json.dumps(payload), usage=usage)


@pytest.fixture
def repos(tmp_path):
    db = str(tmp_path / "s.db")
    tr = TaskRepository(db_path=db); tr.init_schema()
    er = EventRepository(db_path=db); er.init_schema()
    return tr, er


def test_run_task_sync_success(repos):
    tr, er = repos
    task = tr.create_task(
        raw_input="分析坦克300近期的舆情变化",
        parsed_intent={"object": "坦克300", "goal_type": "pulse",
                       "time_range": {"start": "2026-08-01", "end": "2026-08-31"}},
        snapshot=LogicalSnapshot(
            start_time=datetime(2026, 8, 1), end_time=datetime(2026, 8, 31),
            extra={"video_tags": ["坦克300"], "object": "坦克300"},
        ).to_dict(),
    )
    result = run_task_sync(
        task.task_id,
        task_repo=tr, event_repo=er,
        datasource=FakeData(),
        llm_provider=MockLLM(),
        settings=None,
    )
    got = tr.get_task(task.task_id)
    assert got.status == "success"
    assert result["report"]["scope"]["comment_count"] == 100
    assert result["validation"]["rejected_refs"] == []  # c1 在证据库内
    assert result["meta"]["total_tokens"] == 15
    len_events = er.get_events(task.task_id)
    assert any(e.event_type == "tool_call" for e in len_events) or len(len_events) >= 3


def test_run_task_sync_marks_failed_on_error(repos):
    tr, er = repos
    task = tr.create_task(raw_input="x", parsed_intent={"object": "", "goal_type": "pulse"},
                          snapshot={})
    class BoomLLM:
        def chat_json(self, *a, **k):
            raise RuntimeError("boom")
    result = run_task_sync(
        task.task_id, task_repo=tr, event_repo=er,
        datasource=FakeData(), llm_provider=BoomLLM(), settings=None,
    )
    got = tr.get_task(task.task_id)
    assert got.status == "failed"
    assert "boom" in (got.error or "")


def test_run_task_sync_success_progress(repos):
    """Controller ruling P8：结果须含 progress 摘要供前端展示粗粒度进度。"""
    tr, er = repos
    task = tr.create_task(
        raw_input="x", parsed_intent={"object": "坦克300", "goal_type": "pulse"},
        snapshot=LogicalSnapshot(
            start_time=datetime(2026, 8, 1), end_time=datetime(2026, 8, 31),
            extra={"video_tags": ["坦克300"], "object": "坦克300"},
        ).to_dict(),
    )
    result = run_task_sync(
        task.task_id,
        task_repo=tr, event_repo=er,
        datasource=FakeData(),
        llm_provider=MockLLM(),
        settings=None,
    )
    assert result["progress"]["stage"] == "done"
    assert result["progress"]["tool_calls"] == 8
    assert result["progress"]["report_success"] is True


def test_run_task_sync_report_done_event(repos):
    """report_done 事件须携带 LLM usage（total_tokens=15）。"""
    tr, er = repos
    task = tr.create_task(
        raw_input="x", parsed_intent={"object": "坦克300", "goal_type": "pulse"},
        snapshot=LogicalSnapshot(
            start_time=datetime(2026, 8, 1), end_time=datetime(2026, 8, 31),
            extra={"video_tags": ["坦克300"], "object": "坦克300"},
        ).to_dict(),
    )
    run_task_sync(
        task.task_id,
        task_repo=tr, event_repo=er,
        datasource=FakeData(),
        llm_provider=MockLLM(),
        settings=None,
    )
    events = er.get_events(task.task_id)
    report_done = [e for e in events if e.event_type == "report_done"]
    assert len(report_done) == 1
    assert report_done[0].payload["usage"]["total_tokens"] == 15


def test_run_task_sync_no_escape_on_task_missing(repos):
    """任务不存在时返回错误 dict，绝不向 run_task_sync 外抛出异常。"""
    tr, er = repos
    result = run_task_sync(
        "nope", task_repo=tr, event_repo=er,
        datasource=FakeData(), llm_provider=MockLLM(), settings=None,
    )
    assert isinstance(result, dict)
    assert "error" in result


def test_run_task_sync_rerun_finished_is_noop(repos):
    """已完成任务重跑为 no-op：返回 already、不覆盖已存结果、不追加事件。"""
    tr, er = repos
    task = tr.create_task(
        raw_input="x", parsed_intent={"object": "坦克300", "goal_type": "pulse"},
        snapshot=LogicalSnapshot(
            start_time=datetime(2026, 8, 1), end_time=datetime(2026, 8, 31),
            extra={"video_tags": ["坦克300"], "object": "坦克300"},
        ).to_dict(),
    )
    first = run_task_sync(
        task.task_id, task_repo=tr, event_repo=er,
        datasource=FakeData(), llm_provider=MockLLM(), settings=None,
    )
    n_events_after_first = len(er.get_events(task.task_id))
    second = run_task_sync(
        task.task_id, task_repo=tr, event_repo=er,
        datasource=FakeData(), llm_provider=MockLLM(), settings=None,
    )
    assert second == {"already": "success"}
    # 结果未被覆盖（meta.total_tokens 不变），事件流未追加第二条
    got = tr.get_task(task.task_id)
    assert got.status == "success"
    assert got.result["meta"]["total_tokens"] == 15
    assert len(er.get_events(task.task_id)) == n_events_after_first
