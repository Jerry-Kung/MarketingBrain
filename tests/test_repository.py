"""TaskRepository 测试。"""
import sqlite3
import tempfile
import os
from app.store.repository import TaskRepository


def test_task_with_skill_name():
    """测试任务记录携带 skill_name 字段。"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        repo = TaskRepository(db_path)
        repo.init_schema()

        # 测试创建携带 skill_name 的任务
        task = repo.create_task(
            raw_input="测试输入",
            parsed_intent={"goal_type": "pulse"},
            snapshot={"start_time": "2026-09-01"},
            skill_name="opinion-pulse",
        )
        assert task.skill_name == "opinion-pulse"

        # 查询验证
        fetched = repo.get_task(task.task_id)
        assert fetched.skill_name == "opinion-pulse"

        # V0.2 兼容：不传 skill_name
        task2 = repo.create_task(
            raw_input="V0.2 任务",
            parsed_intent={},
            snapshot={},
        )
        assert task2.skill_name is None

        # 验证 list_tasks 也能获取 skill_name
        tasks = repo.list_tasks(limit=10)
        assert any(t.task_id == task.task_id and t.skill_name == "opinion-pulse" for t in tasks)
        assert any(t.task_id == task2.task_id and t.skill_name is None for t in tasks)

        repo.close()
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)


def test_init_schema_migrates_v02_tasks_table(tmp_path):
    """C1 回归：V0.2 tasks 表（无 skill_name 列）经 init_schema 迁移后可正常 get_task。"""
    db_path = str(tmp_path / "v02.db")

    # 模拟 V0.2 建库：tasks 表不含 skill_name 列，并插入一条历史任务
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE tasks (
            task_id      TEXT PRIMARY KEY,
            status       TEXT NOT NULL,
            raw_input    TEXT NOT NULL DEFAULT '',
            parsed_intent TEXT,
            snapshot     TEXT,
            result       TEXT,
            error        TEXT,
            created_at   TEXT NOT NULL,
            updated_at   TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO tasks (task_id, status, raw_input, parsed_intent, snapshot,
                           result, error, created_at, updated_at)
        VALUES ('t1', 'success', '老任务', '{}', '{}', '{}', NULL,
                '2026-08-01T00:00:00+00:00', '2026-08-01T00:00:00+00:00')
        """
    )
    conn.commit()
    conn.close()

    # init_schema 应为已有表补列 skill_name，使 _row_to_task 不再抛 IndexError
    repo = TaskRepository(db_path)
    repo.init_schema()

    task = repo.get_task("t1")
    assert task is not None
    assert task.task_id == "t1"
    assert task.skill_name is None  # V0.2 历史任务无 Skill

    # 已迁移后创建新任务也能携带 skill_name
    new_task = repo.create_task(
        raw_input="新任务",
        parsed_intent={},
        snapshot={},
        skill_name="opinion-pulse",
    )
    assert new_task.skill_name == "opinion-pulse"
    assert repo.get_task(new_task.task_id).skill_name == "opinion-pulse"

    # list_tasks 不再抛错
    assert len(repo.list_tasks(limit=10)) == 2

    repo.close()
