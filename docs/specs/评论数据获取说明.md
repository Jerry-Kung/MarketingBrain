# DriveIntent 用户评论数据获取说明

**面向对象**：大数据舆情分析项目（外部使用方）
**数据库**：MySQL 8.0（utf8mb4）
**数据来源环境**：DriveIntent 测试环境（配置见 `.env`）

本文档说明 DriveIntent 项目中与**用户评论**相关的数据库表结构，以及如何从库中获取评论数据用于舆情分析。

---

## 1. 数据库连接配置

使用下方配置连接测试环境数据库。该配置来源于项目 `.env` 文件：

| 配置项 | 值 |
|--------|-----|
| `DB_HOST` | `118.145.238.55` |
| `DB_PORT` | `3306` |
| `DB_USER` | `driveintent` |
| `DB_PASSWORD` | `Huawei12!@` |
| `DB_NAME` | `drive_intent_backend` |
| 字符集 | `utf8mb4` |

### 1.1 连接字符串

**MySQL 客户端 / 连接器：**

```
mysql -h 118.145.238.55 -P 3306 -u driveintent -p'Huawei12!@' drive_intent_backend
```

**Python（pymysql）：**

```python
import pymysql

conn = pymysql.connect(
    host="118.145.238.55",
    port=3306,
    user="driveintent",
    password="Huawei12!@",
    database="drive_intent_backend",
    charset="utf8mb4",
)
```

**Python（SQLAlchemy）：**

```python
from sqlalchemy import create_engine

engine = create_engine(
    "mysql+pymysql://driveintent:Huawei12%21%40"
    "@118.145.238.55:3306/drive_intent_backend?charset=utf8mb4"
)
```

> **注意**：SQLAlchemy 连接串中密码的 `!` 与 `@` 需 URL-encode（`!` → `%21`，`@` → `%40`）。

---

## 2. 表结构总览

数据库包含 8 张表。**用户评论数据有两条独立的流入路径，对应两类不同的数据主体**：

| 路径 | 数据主体 | 落库表 | 数据规模 | 是否核心 |
|------|---------|--------|---------|:---:|
| **① API 提交** | 前端通过接口提交、项目运行期间持续产生 | `api_job` | **约 97 万条评论** | ✔ 核心 |
| ② V0 导入 | 首次批量导入的样本 | `comment` + `platform_user` + `video` | 约 500 条 | ○ 辅助 |

> **重要**：用于大数据舆情分析的**正式数据在 `api_job` 表**（路径①），并非 `comment` 表。`comment`/`video`/`platform_user` 三表是最初验证用的少量样本（约 500 条，2026-07-20 一次性导入），仅作字段参考，不建议作为分析主体。本文档第 4 节专讲 `api_job` 取数，第 5 节补充 `comment` 三表的辅助查询。

**关联表一览：**

| 表名 | 说明 |
|------|------|
| `api_job` | **核心**：API 异步作业，`request_payload` 存前端提交的评论、`result` 存分析结果 |
| `comment` | V0 导入评论主表（约 500 条样本） |
| `platform_user` | V0 导入评论用户信息（约 384 条样本） |
| `video` | V0 导入视频信息（约 3 条样本） |
| `lead` | 分析出的购车线索 |
| `analysis_task` / `analysis_result` | 分析任务 / 分析结果（V0 路径产物） |
| `llm_call_log` | LLM 调用日志 |

---

## 3. 表结构详解

### 3.1 `api_job` —— API 异步作业（核心，正式数据所在）

`api_job` 是项目面向外部 API 的异步作业表。前端通过 `/api/v1/comment-screening` 提交的评论任务在此落地，**`request_payload` 保存前端提交的原始评论，`result` 保存分析结果**。

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | varchar(36) PK | 作业 ID（UUID） |
| `job_type` | varchar(32) | 作业类型：`comment_screening`（评论初筛）/ `profile_analysis`（账号画像） |
| `status` | varchar(16) | `pending` / `running` / `success` / `partial` / `failed` |
| `request_payload` | json (deferred) | **前端提交的原始数据**（评论列表） |
| `result` | json | 处理结果（与 payload 按下标对齐） |
| `progress_total` | int | 总条数 |
| `progress_done` | int | 已完成条数 |
| `error` | text | 错误信息 |
| `attempt_count` / `max_attempts` | int | 尝试次数 / 上限 |
| `created_at` | datetime | 创建时间（UTC） |
| `updated_at` | datetime | 更新时间（UTC） |
| `finished_at` | datetime | 完成时间（UTC） |
| `lead_grades` | json | V1.7.3 起：每账号内部 HABC 等级，与 `result.results[]` 按下标对应 |

**索引：** `ix_api_job_status_order` (`status`, `attempt_count`, `created_at`)

**`request_payload` 结构**（`comment_screening` 类型）——对应 API 请求体 `CommentScreeningRequest`：

```json
{
  "comments": [
    {
      "comment_id": "7658959462686491419",
      "video_title": "问界S800 ADS 5.0首发，...",
      "video_author": "@老王说车",
      "video_author_fans": 2865000,
      "video_metrics": {
        "like_count": 125000, "comment_count": 3428,
        "share_count": 8900, "collect_count": 12300
      },
      "comment_content": "这车智驾确实牛，上个月刚提的",
      "comment_author": "用户_7823",
      "comment_author_uid": "MS4wLjAB...",
      "comment_time": "2026-07-19T14:23:00+08:00",
      "comment_like_count": 234
    }
  ]
}
```

**`result` 结构**（`comment_screening` 类型）——`results[]` 与 `payload.comments[]` **按下标一一对应**：

```json
{
  "results": [
    {
      "comment_id": "7658959462686491419",
      "passed": true,
      "filter_type": "genuine_user",     // genuine_user | no_purchase_intent | marketing_account | ...
      "filter_reason": null,
      "is_car_owner": true,
      "has_purchase_intent": true,
      "analysis": "该用户已购车且表达明确购车意向...",
      "processed_at": "2026-07-19T22:23:00+08:00",
      "error": null
    }
  ]
}
```

> **关键对齐关系**：`result.results[i]` 与 `request_payload.comments[i]` 按下标对应（V1.7.3 后 `comment_id` 可作为更稳的关联键）。联表时优先用 `comment_id` 关联。

---

### 3.2 `comment` —— 评论主表（V0 样本）

| 字段 | 类型 | NULL | 说明 |
|------|------|:---:|------|
| `id` | int PK auto | 否 | 内部主键 |
| `platform` | varchar(32) | 否 | 来源平台（当前为 `douyin`） |
| `external_id` | varchar(64) | 否 | 平台侧评论 ID（唯一） |
| `video_id` | int FK | 否 | 关联 `video.id` |
| `user_id` | int FK | 否 | 关联 `platform_user.id`（评论者） |
| `content` | text | 否 | 评论文本内容 |
| `comment_time` | datetime | 是 | 评论发布时间 |
| `like_count` | int | 是 | 评论获赞数 |
| `reply_count` | int | 是 | 评论回复数 |
| `is_reply` | tinyint(1) | 是 | 是否为回复评论（1=是） |
| `raw_data` | json | 是 | 平台原始数据快照 |
| `imported_at` | datetime | 否 | 数据导入时间 |

**索引 / 约束：**
- 唯一键 `uq_comment_ext` (`platform`, `external_id`)
- 索引 `ix_comment_video` (`video_id`)
- 索引 `ix_comment_user` (`user_id`)

`raw_data` 示例（抖音原始结构）：

```json
{
  "content": "这车智驾确实牛，我上个月刚提的这款",
  "comment_id": "7661292760368300857",
  "create_time": 1783783725
}
```

> 舆情分析可直接使用 `content` 字段。评论按视频分区，如需全量评论可遍历全部 `video_id` 或按 `imported_at` 增量拉取。

### 3.3 `platform_user` —— 评论用户

| 字段 | 类型 | NULL | 说明 |
|------|------|:---:|------|
| `id` | int PK auto | 否 | 内部主键 |
| `platform` | varchar(32) | 否 | 来源平台 |
| `external_id` | varchar(128) | 否 | 平台侧用户 ID（唯一） |
| `nickname` | varchar(255) | 否 | 用户昵称 |
| `avatar_url` | text | 是 | 用户头像 URL |
| `bio` | text | 是 | 用户简介 |
| `region` | varchar(64) | 是 | 用户地区 |
| `raw_data` | json | 是 | 平台原始数据快照 |
| `imported_at` | datetime | 否 | 数据导入时间 |

**索引 / 约束：**
- 唯一键 `uq_user_ext` (`platform`, `external_id`)

> 联合查询时，通过 `comment.user_id` → `platform_user.id` 关联用户昵称等属性。

### 3.4 `video` —— 视频（评论所属内容）

| 字段 | 类型 | NULL | 说明 |
|------|------|:---:|------|
| `id` | int PK auto | 否 | 内部主键 |
| `platform` | varchar(32) | 否 | 来源平台 |
| `external_id` | varchar(64) | 否 | 平台侧视频 ID（唯一） |
| `title` | text | 否 | 视频标题 |
| `description` | text | 否 | 视频描述 |
| `cover_url` | text | 是 | 视频封面 URL |
| `tags` | json | 是 | 视频标签数组 |
| `author_name` | varchar(255) | 是 | 视频作者昵称 |
| `account_type` | varchar(32) | 是 | 账号类型 |
| `publish_time` | datetime | 是 | 视频发布时间 |
| `transcript` | text | 是 | 视频文字稿 |
| `preset_brand` | varchar(64) | 是 | 预设品牌 |
| `preset_model` | varchar(64) | 是 | 预设车型 |
| `raw_data` | json | 是 | 平台原始数据快照 |
| `imported_at` | datetime | 否 | 数据导入时间 |

**索引 / 约束：**
- 唯一键 `uq_video_ext` (`platform`, `external_id`)

> 通过 `comment.video_id` → `video.id` 关联到视频标题、作者、品牌等上下文，用于按品牌/视频维度做舆情聚合。

### 3.5 `lead` —— 线索（可选，分析结果）

评论经 DriveIntent 分析后，若被识别为购车线索，会在 `lead` 表落地。可选用。

| 字段 | 类型 | NULL | 说明 |
|------|------|:---:|------|
| `id` | int PK auto | 否 | 内部主键 |
| `user_id` | int FK | 否 | 关联 `platform_user.id` |
| `grade` | varchar(4) | 否 | 线索等级：`H`/`A`/`B`/`C` |
| `is_valid` | tinyint(1) | 否 | 是否有效 |
| `status` | varchar(16) | 否 | 状态（如 `new`） |
| `target_brands` | json | 是 | 意向品牌数组 |
| `target_models` | json | 是 | 意向车型数组 |
| `intent_models` | json | 是 | 意向车型识别（V1.8.0） |
| `intent_model_category` | varchar(4) | 是 | 车型分类码 |
| `summary` | text | 否 | 线索摘要 |
| `purchase_stage` | varchar(64) | 是 | 购车阶段 |
| `core_needs` | json | 是 | 核心需求 |
| `main_concerns` | json | 是 | 主要顾虑 |
| `purchase_time` | varchar(64) | 是 | 购车时间预期 |
| `usage_scenario` | varchar(255) | 是 | 使用场景 |
| `entry_point` | text | 是 | 销售开场白 |
| `verification_questions` | json | 是 | 验证问题 |
| `evidence` | json | 是 | 证据（含引用的 comment） |
| `confidence` | float | 是 | 置信度 |
| `skill_version` | varchar(16) | 否 | 技能版本 |
| `review_status` | varchar(16) | 否 | 复核状态 |
| `review_tags` | json | 是 | 复核标签 |
| `review_note` | text | 是 | 复核备注 |
| `created_at` | datetime | 否 | 创建时间 |
| `updated_at` | datetime | 否 | 更新时间 |

**索引 / 约束：**
- 唯一键 `ix_lead_user` (`user_id`)

---

## 4. `api_job` 取数（正式数据，重点）

`request_payload` / `result` 均为 MySQL JSON 列，可直接用 JSON 函数透视，无需逐行解析。**文档中的 JSON_TABLE 语法已在测试环境实测通过**（MySQL 8.0.4+ 支持）。

### 4.1 拉取全部评论 + 初筛结果（推荐）

每行 = 一条评论，带所属作业与初筛判定：

```sql
SELECT
    j.id                                  AS job_id,
    j.status                              AS job_status,
    j.created_at                          AS job_created_at,
    c.cid                                 AS comment_id,
    c.c                                   AS comment_content,
    c.vt                                  AS video_title,
    c.at                                  AS comment_author,
    c.uid                                 AS comment_author_uid,
    c.like                                AS comment_like_count,
    r.passed,
    r.is_car_owner,
    r.has_purchase_intent,
    r.analysis
FROM api_job j
CROSS JOIN JSON_TABLE(          -- 展开 payload.comments[]
    j.request_payload, '$.comments[*]'
    COLUMNS (
        cid  VARCHAR(64)      PATH '$.comment_id',
        vt   TEXT             PATH '$.video_title',
        at   VARCHAR(255)     PATH '$.comment_author',
        uid  VARCHAR(128)     PATH '$.comment_author_uid',
        c    TEXT             PATH '$.comment_content',
        like INT              PATH '$.comment_like_count'
    )
) c
LEFT JOIN JSON_TABLE(         -- 展开 result.results[]，按 comment_id 关联
    j.result, '$.results[*]'
    COLUMNS (
        rcid VARCHAR(64)      PATH '$.comment_id',
        passed BOOLEAN        PATH '$.passed',
        is_car_owner BOOLEAN  PATH '$.is_car_owner',
        has_purchase_intent BOOLEAN PATH '$.has_purchase_intent',
        analysis TEXT         PATH '$.analysis'
    )
) r ON c.cid = r.rcid
WHERE j.job_type = 'comment_screening'
  AND j.status = 'success';    -- 只看成功作业；如需含失败/部分请去掉
```

> 注：`passed BOOLEAN` 列在 MySQL 里实际存取为 1/0（tinyint），下游按 0 否 1 是处理。

### 4.2 只取评论文本（舆情分析最常用，轻量）

```sql
SELECT
    j.id          AS job_id,
    c.cid         AS comment_id,
    c.c           AS comment_content,
    c.vt          AS video_title,
    c.at          AS comment_author
FROM api_job j
CROSS JOIN JSON_TABLE(
    j.request_payload, '$.comments[*]'
    COLUMNS (
        cid  VARCHAR(64)  PATH '$.comment_id',
        vt   TEXT         PATH '$.video_title',
        at   VARCHAR(255) PATH '$.comment_author',
        c    TEXT         PATH '$.comment_content'
    )
) c
WHERE j.job_type = 'comment_screening'
  AND j.status = 'success';
```

### 4.3 按时间增量拉取（舆情分析推荐）

作业按 `created_at`（UTC）计。建议记录游标时间，增量抓取：

```sql
SELECT
    j.id          AS job_id,
    c.cid         AS comment_id,
    c.c           AS comment_content,
    c.vt          AS video_title
FROM api_job j
CROSS JOIN JSON_TABLE(
    j.request_payload, '$.comments[*]'
    COLUMNS (
        cid  VARCHAR(64)  PATH '$.comment_id',
        vt   TEXT         PATH '$.video_title',
        c    TEXT         PATH '$.comment_content'
    )
) c
WHERE j.job_type = 'comment_screening'
  AND j.status = 'success'
  AND j.created_at > :last_created_at   -- 上次拉取时间点（UTC）
ORDER BY j.created_at;
```

### 4.4 统计评论总量（验证数据规模）

```sql
SELECT
    COUNT(*)                          AS job_count,
    COALESCE(SUM(JSON_LENGTH(request_payload, '$.comments')), 0) AS comment_count
FROM api_job
WHERE job_type = 'comment_screening';
```

> 实测：测试库 `drive_intent_backend` 中 `comment_screening` 作业约 7 万个，累计评论约 **97 万条**（成功作业约 81 万条），时间跨度约 2026-07-24 至 2026-09-03。

### 4.5 若 JSON_TABLE 不可用（兼容降级）

MySQL 8.0 以下无 `JSON_TABLE`，可用 `JSON_EXTRACT` 逐下标取（条数固定时才实用）：

```sql
SELECT
    j.id,
    JSON_UNQUOTE(JSON_EXTRACT(j.request_payload, '$.comments[0].comment_content')) AS comment_0
FROM api_job j
WHERE j.job_type = 'comment_screening';
```

---

## 5. `comment` 表辅助取数（V0 样本）

以下 SQL 针对 `comment` 表（V0 导入的约 500 条样本）。若需正式数据，请优先使用第 4 节 `api_job` 方案。

### 5.1 按视频拉取全部评论

```sql
SELECT
    c.id,
    c.external_id              AS comment_id,
    c.content,
    c.comment_time,
    c.like_count,
    c.reply_count,
    c.is_reply,
    u.nickname                 AS user_nickname,
    u.external_id              AS user_external_id,
    v.title                    AS video_title,
    v.author_name              AS video_author,
    v.preset_brand             AS brand,
    v.preset_model             AS model
FROM comment c
LEFT JOIN platform_user u ON c.user_id = u.id
LEFT JOIN video v ON c.video_id = v.id
WHERE c.video_id = :video_id   -- 指定视频内部 ID
ORDER BY c.comment_time;
```

### 5.2 按时间增量拉取（comment 表）

```sql
SELECT
    c.id,
    c.external_id       AS comment_id,
    c.content,
    c.comment_time,
    c.like_count,
    c.reply_count,
    c.is_reply,
    c.raw_data,
    u.nickname          AS user_nickname,
    u.region            AS user_region,
    v.title             AS video_title,
    v.author_name       AS video_author,
    v.preset_brand      AS brand
FROM comment c
LEFT JOIN platform_user u ON c.user_id = u.id
LEFT JOIN video v ON c.video_id = v.id
WHERE c.imported_at > :last_imported_at   -- 上次拉取时间点
ORDER BY c.imported_at;
```

### 5.3 全量导出评论文本

```sql
SELECT
    c.external_id       AS comment_id,
    c.content,
    c.comment_time,
    c.like_count,
    c.reply_count,
    c.is_reply,
    u.nickname          AS user_nickname,
    v.title             AS video_title,
    v.author_name       AS video_author,
    v.preset_brand      AS brand,
    v.preset_model      AS model
FROM comment c
LEFT JOIN platform_user u ON c.user_id = u.id
LEFT JOIN video v ON c.video_id = v.id
ORDER BY c.comment_time;
```

### 5.4 关联线索（已识别购车意图的评论）

```sql
SELECT
    c.content,
    c.comment_time,
    u.nickname,
    l.grade,
    l.target_brands,
    l.purchase_stage,
    l.summary
FROM comment c
LEFT JOIN platform_user u ON c.user_id = u.id
LEFT JOIN lead l ON u.id = l.user_id
WHERE l.grade IS NOT NULL;
```

### 5.5 按品牌聚合评论数（示例）

```sql
SELECT
    v.preset_brand AS brand,
    COUNT(*)      AS comment_count
FROM comment c
JOIN video v ON c.video_id = v.id
GROUP BY v.preset_brand
ORDER BY comment_count DESC;
```

---

## 6. 注意事项

1. **正式数据源**：大数据舆情分析的正式数据在 `api_job` 表（约 97 万条评论），可放心使用。`comment` 三表（约 500 条）仅为 V0 首批样本，建议作字段参考。
2. **增量拉取**：`api_job` 用 `created_at`（UTC）做增量；`comment` 表用 `imported_at`。
3. **编码**：数据库为 `utf8mb4`。请确保连接字符集为 `utf8mb4`，否则中文内容可能乱码。
4. **JSON 字段**：`request_payload` / `result` / `raw_data` 等为 JSON 类型，读取时在应用层解析或用 JSON 函数透视。
5. **`is_reply`**：标识是否为回复评论（1=是）。部分历史数据可能为 NULL。
6. **只读访问**：数据来自本项目正式应用库，**请使用只读账号**（仅授 `SELECT`），确保只读不以任何形式写库。创建示例：
   ```sql
   CREATE USER 'readonly_analyst'@'%' IDENTIFIED BY '强密码';
   GRANT SELECT ON drive_intent_backend.* TO 'readonly_analyst'@'%';
   FLUSH PRIVILEGES;
   ```
7. **敏感信息**：数据库凭据请在内部安全传递。此配置为测试环境，若用于生产请使用正式配置。

---

## 7. 数据导入流程（供理解数据来源）

### 7.1 API 提交（正式数据主渠道）

前端通过 API 提交任务（`/api/v1/comment-screening`），后端将请求体完整存入 `api_job.request_payload`，交由 API Worker 异步处理，结果写回 `api_job.result`。数据实时产生、持续累积。

### 7.2 V0 批量导入（样本数据）

初始样本由 Importer 从平台（抖音）爬取压缩包导入（`app/importer/core.py`），批量写入 `video`、`platform_user`、`comment` 三表，规则如下：

- 三表按 `external_id` 去重（唯一键）
- 评论通过 `video_external_id` / `user_external_id` 映射到内部 `video_id` / `user_id`
- 若对应视频或用户不存在，该条评论被跳过
- `content` 为空字符串的评论会被剔除（V1.8.5）

因此，`comment` 表中每条记录都保证关联到有效的视频与用户。
