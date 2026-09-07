"""config.py 的测试。

测试通过真实 .env 加载和缺项提示，验证 app.core.config.Settings 的行为。
"""
import os

import pytest

from app.core.config import Settings, load_settings, PROJECT_ROOT


@pytest.fixture
def backup_env():
    """备份并清理与环境相关的旧值，测试后恢复。"""
    saved = {
        k: os.environ.get(k)
        for k in [
            "APP_ENV", "APP_PORT", "APP_LOG_LEVEL", "APP_STATE_DIR",
            "DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_NAME", "DB_CHARSET",
            "UI_POLL_INTERVAL_MS", "TOOL_MAX_RECORDS", "LLM_TIMEOUT_MS",
        ]
    }
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def test_loads_from_env_when_present(backup_env, monkeypatch):
    """当.env存在且可读时，应成功加载并给出数据库配置。"""
    # 使用真实项目 .env
    s = load_settings()
    assert s.DB_HOST
    assert s.DB_PORT == 3306
    assert s.DB_USER
    assert s.DB_NAME == "drive_intent_backend"


def test_settings_have_required_fields(backup_env):
    """Settings 应包含核心配置项字段。"""
    s = Settings()
    assert hasattr(s, "APP_ENV")
    assert hasattr(s, "APP_PORT")
    assert hasattr(s, "DB_HOST")
    assert hasattr(s, "DB_PORT")
    assert hasattr(s, "DB_USER")
    assert hasattr(s, "DB_NAME")
    assert hasattr(s, "DB_CHARSET")
    assert hasattr(s, "APP_STATE_DIR")


def test_db_url_property(backup_env):
    """Settings 应生成 SQLAlchemy 连接串，并 URL-encode 密码特殊字符。"""
    s = Settings()
    url = s.db_url
    assert url.startswith("mysql+pymysql://")
    assert s.DB_NAME in url
    assert s.DB_CHARSET in url


def test_missing_env_raises_clear_error(backup_env, monkeypatch):
    """缺少必须的 DB 配置时应抛出清晰错误，而非静默缺省。"""
    # 显式指定不从 .env 读取，并清空环境变量，模拟完全无配置场景
    for k in ["DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_NAME", "DB_CHARSET"]:
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(ValueError) as exc_info:
        Settings(_env_file=None)
    assert "DB_" in str(exc_info.value)
    assert ".env" in str(exc_info.value)


def test_llm_timeout_default(backup_env):
    s = Settings()
    assert s.LLM_TIMEOUT_MS == 120000
    assert s.llm_timeout == 120.0


def test_llm_timeout_override(backup_env, monkeypatch):
    monkeypatch.setenv("LLM_TIMEOUT_MS", "60000")
    s = Settings()
    assert s.LLM_TIMEOUT_MS == 60000
    assert s.llm_timeout == 60.0


def test_v03_workflow_config():
    """测试 V0.3 工作流配置加载与默认值。"""
    settings = Settings(
        DB_HOST="localhost", DB_PORT=3306, DB_USER="test", DB_PASSWORD="pwd", DB_NAME="db",
        LLM_API_BASE="https://api.example.com", LLM_API_KEY="key", LLM_MODEL="model",
    )
    assert settings.SKILLS_DIR == "skills"
    assert settings.DEFAULT_SKILL == "opinion-pulse"
    assert settings.WORKFLOW_STAGE_TIMEOUT_MS == 300000
    assert settings.ENABLE_WORKFLOW_ENGINE is True
    assert settings.workflow_stage_timeout == 300.0

    # skills_dir 相对路径锚定到项目根，不依赖进程 CWD
    assert settings.skills_dir == PROJECT_ROOT / "skills"
    assert settings.skills_dir.is_absolute()
    assert settings.skills_dir.exists()
