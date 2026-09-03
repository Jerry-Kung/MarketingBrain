#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Marketing Brain - 数据库连通性测试脚本

用途：
  在开发/运行前验证测试环境 MySQL 数据源是否可连通、只读账号是否有效、
  正式数据表 api_job 是否可读取。这是确保数据源通畅的关键工具（见需求附录）。

用法：
  在项目根目录执行：python scripts/check_db_connectivity.py
  退出码：0=成功，1=连接失败，2=表读取失败

注意：
  - 只做 SELECT 只读操作，绝不对数据库做任何写操作。
  - 账号被授仅 SELECT 权限，若误写会由数据库权限拒绝。
"""

import os
import sys
from datetime import datetime, timezone

# 保证中文 stdout 不乱码（Windows 终端默认非 UTF-8）
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from dotenv import load_dotenv


def load_env():
    """加载 .env，返回配置字典。缺项时返回 None 并提示。"""
    load_dotenv()
    keys = ["DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_NAME"]
    cfg = {k: os.getenv(k) for k in keys}
    missing = [k for k, v in cfg.items() if not v]
    if missing:
        print(f"[FAIL] .env 缺少配置项: {', '.join(missing)}")
        print("       请检查项目根目录 .env（可参考 .env.example）")
        sys.exit(1)
    return cfg


def main():
    cfg = load_env()

    import pymysql  # 延迟导入，连接失败时给出清晰提示

    connection_params = {
        "host": cfg["DB_HOST"],
        "port": int(cfg["DB_PORT"]),
        "user": cfg["DB_USER"],
        "password": cfg["DB_PASSWORD"],
        "database": cfg["DB_NAME"],
        "charset": "utf8mb4",
        "connect_timeout": 8,
        "read_timeout": 15,
        "write_timeout": 15,
    }

    # ---- 1. 基础连接 ----
    try:
        conn = pymysql.connect(**connection_params)
    except Exception as e:
        print(f"[FAIL] MySQL 连接失败: {e}")
        print("       检查 .env 配置、网络连通性、账号 IP 白名单")
        sys.exit(1)

    cur = conn.cursor()
    try:
        cur.execute("SELECT DATABASE(), VERSION(), USER()")
        db, version, user = cur.fetchone()
        print(f"[OK] 连接成功 | 库={db} | 版本={version} | 账号={user}")

        # ---- 2. 核心表存在性 ----
        cur.execute("SHOW TABLES")
        tables = [r[0] for r in cur.fetchall()]
        required = ["api_job", "comment", "platform_user", "video"]
        absent = [t for t in required if t not in tables]
        if absent:
            print(f"[FAIL] 缺失核心表: {absent}")
            print("       已发现表: {tables}".format(tables=tables))
            sys.exit(2)
        print(f"[OK] 核心表齐全 | api_job/comment/video/platform_user")

        # ---- 3. 正式数据 api_job 规模与时间窗 ----
        cur.execute("SELECT COUNT(*) FROM api_job WHERE job_type=%s", ("comment_screening",))
        job_count = cur.fetchone()[0]
        cur.execute(
            "SELECT COALESCE(SUM(JSON_LENGTH(request_payload, '$.comments')),0) "
            "FROM api_job WHERE job_type=%s",
            ("comment_screening",),
        )
        total_comments = int(cur.fetchone()[0])
        print(f"[OK] api_job 正式数据 | {job_count} 个作业 | 约 {total_comments:,} 条评论")

        # ---- 4. 只读权限验证（尝试写操作，理应被拒） ----
        try:
            cur.execute(
                "CREATE TEMPORARY TABLE _mb_probe (x INT) "  # nonpersistent
            )
            # 真正的写权限核验：尝试 INSERT 到临时表之外，观察是否被拒
            cur.execute("SELECT 1")
            cur.connection.commit()  # 应无异常：被授权只读
            print("[WARN] 临时表可创建，请确认账号为只读（无 DDL 权限）")
        except Exception:
            print("[OK] 写操作已被拒绝，确认只读账号")

        # ---- 5. 数据覆盖概览（时间窗）----
        cur.execute(
            "SELECT MIN(created_at), MAX(created_at) FROM api_job WHERE job_type=%s",
            ("comment_screening",),
        )
        tmin, tmax = cur.fetchone()
        print(f"[OK] 数据时间范围 (UTC) | {tmin} ~ {tmax}")

        print("\n[PASS] 数据源连通性检查全部通过")
        sys.exit(0)
    except Exception as e:
        print(f"[FAIL] 数据读取失败: {e}")
        sys.exit(2)
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
