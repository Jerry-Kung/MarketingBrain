"""TaskRepository 测试。"""
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
