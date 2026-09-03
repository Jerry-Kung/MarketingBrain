"""MySQL 只读数据源集成测试。

这些测试连接真实测试环境 MySQL（读取 .env 配置），验证数据链路。
默认通过 `-m integration` 标记运行，避免普通单测依赖数据库。

需要在项目 .env 配置了可用的只读 MySQL 账号。
"""
import os
from datetime import datetime

import pytest

pytestmark = pytest.mark.integration


def _settings():
    from app.core.config import Settings
    return Settings()


@pytest.fixture(scope="module")
def settings():
    return _settings()


@pytest.fixture(scope="module")
def engine(settings):
    from sqlalchemy import create_engine
    # pool_pre_ping 保证连接存活检查；池大小受控防连锁占用
    eng = create_engine(
        settings.db_url,
        pool_pre_ping=True,
        pool_recycle=1800,
        pool_size=3,
        max_overflow=2,
        echo=False,
    )
    yield eng
    eng.dispose()


@pytest.fixture(scope="module")
def ds(engine):
    from app.datasource.adapter import MySqlDataSource
    return MySqlDataSource(engine)


def test_connectivity_and_count(engine):
    """能连接数据库并统计评论总量。"""
    from sqlalchemy import text
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT COUNT(*) AS c, "
                 "COALESCE(SUM(JSON_LENGTH(request_payload, '$.comments')),0) AS n "
                 "FROM api_job WHERE job_type='comment_screening' AND status='success'")
        ).one()
    assert row.c > 0
    assert row.n > 1000  # 正式数据远大于样本


def test_fetch_comments(ds):
    """能从 api_job 拉取评论，返回 CommentRecord 列表。"""
    records = ds.fetch_comments(limit=20)
    assert len(records) > 0
    rec = records[0]
    assert hasattr(rec, "comment_id")
    assert rec.comment_id  # 非空
    # 成功作业的评论应有关联初筛结果（passed 非 None）或极少数 None
    assert rec.job_id


def test_fetch_comments_respects_limit(ds):
    """limit 参数应精确限制返回条数。"""
    records = ds.fetch_comments(limit=5)
    assert len(records) == 5


def test_data_overview(ds):
    """数据概览应返回帖子数、评论数、时间范围。"""
    overview = ds.data_overview()
    assert overview.job_count > 0
    assert overview.comment_count > 1000
    assert overview.start_time is not None
    assert overview.end_time is not None
    assert isinstance(overview.start_time, datetime)
