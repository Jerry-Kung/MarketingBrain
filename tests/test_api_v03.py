"""API V0.3 集成测试（Skill 工作流）。"""
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tempfile

from fastapi.testclient import TestClient


def test_create_task_with_workflow():
    """测试创建任务时选择 Skill 并启动工作流（ENABLE_WORKFLOW_ENGINE=True）。"""
    from app.api.routes import create_app
    from app.core.config import Settings

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    settings = Settings(
        DB_HOST="localhost", DB_PORT=3306, DB_USER="test",
        DB_PASSWORD="pwd", DB_NAME="db",
        LLM_API_BASE="https://api.example.com",
        LLM_API_KEY="key", LLM_MODEL="model",
        APP_STATE_DIR=str(Path(db_path).parent),
        ENABLE_WORKFLOW_ENGINE=True,
        SKILLS_DIR="skills",
    )

    # background=True 会触发工作流路径，但 datasource=None 让任务保持 pending（不启动线程）
    # 只验证 skill_name 字段被正确填充
    app = create_app(
        db_path=db_path,
        datasource=None,
        llm_provider=object(),  # 非 None，绕过 LLM 未配置检查
        background=True,
        settings_override=settings,
    )
    client = TestClient(app)

    response = client.post("/api/tasks", json={"raw_input": "分析坦克300近期舆情"})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "pending"
    assert data["skill_name"] == "opinion-pulse"  # V0.3 默认 Skill


def test_v02_baseline_mode():
    """测试关闭 ENABLE_WORKFLOW_ENGINE 时回退到 V0.2 基线（skill_name 为 None）。"""
    from app.api.routes import create_app
    from app.core.config import Settings

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    settings = Settings(
        DB_HOST="localhost", DB_PORT=3306, DB_USER="test",
        DB_PASSWORD="pwd", DB_NAME="db",
        LLM_API_BASE="https://api.example.com",
        LLM_API_KEY="key", LLM_MODEL="model",
        APP_STATE_DIR=str(Path(db_path).parent),
        ENABLE_WORKFLOW_ENGINE=False,
        SKILLS_DIR="skills",
    )

    app = create_app(
        db_path=db_path,
        datasource=None,
        llm_provider=object(),  # 非 None，绕过 LLM 未配置检查
        background=True,
        settings_override=settings,
    )
    client = TestClient(app)

    response = client.post("/api/tasks", json={"raw_input": "分析坦克300近期舆情"})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "pending"
    assert data["skill_name"] is None  # V0.2 模式无 Skill
