"""Marketing Brain 配置模块。

从 .env / 环境变量加载运行配置。业务配置（数据库连接等）必须显式提供，
否则抛出清晰错误；应用运行参数提供合理默认值。
"""
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录（app/core/config.py -> ../../）
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 必须显式提供的数据库配置
REQUIRED_DB_FIELDS = ("DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_NAME")


class Settings(BaseSettings):
    """应用配置。

    提供两种构造方式：
    - Settings()                从 .env / 环境变量读取，缺失必需项时抛出 ValueError
    - Settings(**overrides)     显式传值覆盖，用于测试或程序化配置
    """

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    # ---------- 应用运行 ----------
    APP_ENV: str = Field(default="development")
    APP_PORT: int = Field(default=19783)
    APP_LOG_LEVEL: str = Field(default="info")

    # ---------- 应用状态存储（SQLite 持久化目录） ----------
    APP_STATE_DIR: str = Field(default=".data/state")

    # ---------- 测试环境 MySQL（只读账号，仅 SELECT） ----------
    DB_HOST: str | None = None
    DB_PORT: int | None = None
    DB_USER: str | None = None
    DB_PASSWORD: str | None = None
    DB_NAME: str | None = None
    DB_CHARSET: str = Field(default="utf8mb4")

    # ---------- LLM（V0.2 接入，V0.1 预留） ----------
    LLM_API_BASE: str | None = None
    LLM_API_KEY: str | None = None
    LLM_MODEL: str | None = None

    # ---------- 运行参数 ----------
    UI_POLL_INTERVAL_MS: int = Field(default=2500)
    TOOL_MAX_RECORDS: int = Field(default=500)

    @field_validator("APP_PORT", "UI_POLL_INTERVAL_MS", "TOOL_MAX_RECORDS")
    @classmethod
    def _positive_int(cls, v: int) -> int:
        if v <= 0:
            raise ValueError(f"配置项必须为正整数，收到 {v}")
        return v

    def _validate_required_db(self) -> None:
        """校验数据库配置是否齐全。缺项时列出并报错。"""
        missing = [f for f in REQUIRED_DB_FIELDS if getattr(self, f) in (None, "")]
        if missing:
            raise ValueError(
                "数据库配置不完整，缺少: " + ", ".join(missing)
                + "。请在 .env 中配置（可参考 .env.example）。"
            )

    @property
    def db_url(self) -> str:
        """生成 SQLAlchemy 连接串，密码特殊字符做 URL-encode。"""
        from urllib.parse import quote_plus

        self._validate_required_db()
        pwd = quote_plus(self.DB_PASSWORD)
        return (
            f"mysql+pymysql://{self.DB_USER}:{pwd}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}?charset={self.DB_CHARSET}"
        )

    def model_post_init(self, __context) -> None:
        """在对象初始化完成后校验必需数据库配置。"""
        self._validate_required_db()


def load_settings() -> Settings:
    """加载配置。缺失数据库配置时抛出清晰 ValueError。"""
    return Settings()
