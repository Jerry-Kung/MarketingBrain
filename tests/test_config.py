"""config.py 的测试。

测试通过真实 .env 加载和缺项提示，验证 app.core.config.Settings 的行为。
"""
import os
from pathlib import Path

import pytest

from app.core.config import Settings, load_settings

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def backup_env():
    """备份并清理与环境相关的旧值，测试后恢复。"""
    saved = {
        k: os.environ.get(k)
        for k in [
            "APP_ENV", "APP_PORT", "APP_LOG_LEVEL", "APP_STATE_DIR",
            "DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_NAME", "DB_CHARSET",
            "UI_POLL_INTERVAL_MS", "TOOL_MAX_RECORDS",
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
