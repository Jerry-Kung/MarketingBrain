"""SQLite 持久化层测试。

V0 应用状态用 SQLite + 本地持久化目录，保存任务、事件、证据索引和结果。
通过 Repository 接口为 V1 更换数据库留口。
"""
import os
from datetime import datetime

import pytest

from app.store.repository import (
    TaskRecord,
    TaskRepository,
    EventRepository,
    EventRecord,
)


@pytest.fixture
def temp_db(tmp_path):
    db_path = os.path.join(tmp_path, "test_state.db")
    return db_path


@pytest.fixture
def repo(temp_db):
    r = TaskRepository(db_path=temp_db)
    r.init_schema()
    yield r
    r.close()


class TestTaskRepository:
    """任务持久化。"""

    def test_create_task(self, repo):
        task = repo.create_task(
            raw_input="分析坦克300近期的舆情变化",
            parsed_intent={"object": "坦克300", "goal_type": "pulse"},
            snapshot={"datasource": "api_job"},
        )
        assert task.task_id is not None
        assert task.status == "pending"
        assert repo.get_task(task.task_id) is not None

    def test_update_status(self, repo):
        task = repo.create_task(raw_input="test", parsed_intent=None, snapshot=None)
        repo.update_status(task.task_id, "running")
        got = repo.get_task(task.task_id)
        assert got.status == "running"

    def test_list_tasks(self, repo):
        repo.create_task(raw_input="a", parsed_intent=None, snapshot=None)
        repo.create_task(raw_input="b", parsed_intent=None, snapshot=None)
        tasks = repo.list_tasks()
        assert len(tasks) == 2

    def test_save_result(self, repo):
        task = repo.create_task(raw_input="test", parsed_intent=None, snapshot=None)
        repo.save_result(task.task_id, {"report": "ok", "samples": 100})
        got = repo.get_task(task.task_id)
        assert got is not None
        # result 应能保留结构化数据
        assert got.result.get("samples") == 100


class TestEventRepository:
    """运行事件持久化（追加式审计）。"""

    def test_append_event(self, temp_db):
        er = EventRepository(db_path=temp_db)
        er.init_schema()
        ev = er.append_event(
            task_id="task-1",
            event_type="tool_call",
            payload={"tool": "fetch_comments", "summary": "fetch 20"},
        )
        assert ev.event_id is not None
        assert er.get_events("task-1")  # 非空
        er.close()

    def test_get_events_ordered(self, temp_db):
        er = EventRepository(db_path=temp_db)
        er.init_schema()
        er.append_event("t1", "start", {})
        er.append_event("t1", "end", {})
        events = er.get_events("t1")
        assert len(events) == 2
        assert events[0].event_type == "start"
        assert events[1].event_type == "end"
        er.close()

    def test_event_payload_json_safe(self, temp_db):
        er = EventRepository(db_path=temp_db)
        er.init_schema()
        er.append_event("t1", "evidence", {"ids": [1, 2, 3]})
        events = er.get_events("t1")
        assert events[0].payload["ids"] == [1, 2, 3]
        er.close()
