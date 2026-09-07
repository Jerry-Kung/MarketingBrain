# Marketing Brain V0.2 技术报告：从输入问题到舆情策略包

> 面向人类的运行技术说明。按「用户输入 → 报告落库」的顺序，把每一步的输入、输出与核心处理逻辑写清楚：
> 数据节点给出实际 SQL，LLM 节点给出实际 prompt。
> 实现细节在各 `app/` 模块，本报告不重复函数签名，只讲链路本身。

## 0. 结论先行

V0.2 是一条**非 Agent 基线**流水线，全程无模型自主规划：

```
一句话(自然语言)
→ ① 规则解析意图        (无 LLM、无 DB)
→ ② 逻辑快照 + 建任务   (无 LLM、无 DB，一次 SQLite 写入)
→ ③ 确定性取证:8 个工具 / 9 次只读 DB 查询
→ ④ 1 次真实 LLM 调用:生成固定结构「舆情策略包」
→ ⑤ 报告引用校验        (无 LLM、无 DB，对照内存证据库)
→ ⑥ 报告 + 证据 + 校验 + Token 落库(SQLite)
```

规格：规则解析 1 次、DB 只读查询 9 次、LLM 调用 1 次、SQLite 写入若干次、内存校验 1 次。
除「报告生成」外，其余步骤全部是确定性代码，可复现、可审计。

---

## 1. 整条链路一览

| # | 步骤 | 类型 | 输入 → 输出 | 模块 |
|---|------|------|-------------|------|
| 1 | 任务理解（意图识别） | 规则+关键词 | `raw_input` 文本 → `AnalysisIntent` | `understanding/intent.py` |
| 2 | 逻辑快照 + 任务创建 | 纯逻辑 + SQLite | `AnalysisIntent` → `snapshot` + `task` | `snapshot/snapshot.py`、`store/repository.py` |
| 3 | 数据取证（8 工具） | 9 次只读 DB 查询 | `snapshot` → `AnalysisBundle`（统计+抽样+证据） | `analysis/tools.py`、`datasource/adapter.py` |
| 4 | 报告生成 | **1 次真实 LLM** | `bundle`+`intent` → 报告 JSON | `llm/report.py`、`llm/provider.py` |
| 5 | 报告引用校验 | 纯逻辑 | 报告 JSON → `(cleaned_report, validation)` | `pipeline/verify.py` |
| 6 | 落库与返回 | SQLite | 报告+校验+证据+meta → `result` | `pipeline/runner.py` |

执行模型：`POST /api/tasks` 立即返回 `task_id`，第 3~5 步在**后台线程**执行，前端按
`UI_POLL_INTERVAL_MS`（默认 2500ms）轮询状态。任一步出错 → 任务标记 `failed` 并保留错误，**不生成伪报告**。

---

## 2. 各步骤详解

### 2.1 任务理解（意图识别）—— 无 LLM、无 DB

- **输入**：用户一句自由文本，如 `分析坦克300近期的舆情变化`。
- **输出**：`AnalysisIntent`（解析后存为 dict）：

```json
{
  "object": "坦克300",
  "goal_type": "pulse",
  "time_range": {"start": "2026-08-05", "end": "2026-09-04"},
  "raw_text": "分析坦克300近期的舆情变化"
}
```

- **核心逻辑**（`IntentParser.parse`）：
  1. **分析对象**：内置关键词表（`BUILTIN_KEYWORDS`）按**长度降序**匹配，优先命中「猛士M817」这类复合词而非「猛士」。
  2. **时间范围**：相对短语规则解析，「近期/最近」= 近 30 天，「本周/上周/上个月/最近 N 天/YYYY年M月」均有对应规则；无时间短语时由路由补默认（近 30 天）。
  3. **目标类型**：触发词判别——「排查/异常/升温/突发」→ `anomaly`；「专题/下钻/深入/对比」→ `drill`；否则 `pulse`。

### 2.2 逻辑快照 + 任务创建 —— 无 LLM、无 DB（一次 SQLite 写）

- **输入**：上一步的 `AnalysisIntent`。
- **输出**：`snapshot`（存入任务记录）：

```json
{
  "datasource": "api_job",
  "start_time": "2026-08-05T00:00:00",
  "end_time": "2026-09-04T23:59:59",
  "max_comment_id": null,
  "created_at": "2026-09-04T10:00:00+00:00",
  "extra": {"object": "坦克300", "goal_type": "pulse", "video_tags": ["坦克300"]}
}
```

- **核心逻辑**：
  - 把意图的日期区间转为 `datetime` 窗口：起点 `00:00:00`、终点 `23:59:59`（`LogicalSnapshot.from_intent`）。
  - 分析对象写入 `extra.video_tags`（路由 P10：对象按 `#标签` 匹配），后续所有工具共用这组标签，保证一次运行口径一致。
  - 快照**只记边界不拷贝数据**；同时 `TaskRepository.create_task` 写入一条 `pending` 任务记录。

### 2.3 数据取证：8 个确定性工具 / 9 次只读查询 —— LLM 不参与

所有查询共用一张**基础展开查询**（在 MySQL 端用 `JSON_TABLE` 把 `api_job.request_payload` 的评论数组展开，
并与 `api_job.result` 的初筛结果按 `comment_id` 关联）。`like` 是 MySQL 保留字，列别名需反引号：

```sql
SELECT
    j.id AS job_id, j.status AS job_status, j.created_at AS job_created_at,
    c.cid AS comment_id, c.c AS comment_content, c.vt AS video_title,
    c.at AS comment_author, c.uid AS comment_author_uid,
    c.like_count AS comment_like_count,
    r.passed, r.is_car_owner, r.has_purchase_intent, r.analysis
FROM api_job j
CROSS JOIN JSON_TABLE(
    j.request_payload, '$.comments[*]'
    COLUMNS (
        cid VARCHAR(64) PATH '$.comment_id',     vt TEXT PATH '$.video_title',
        at  VARCHAR(255) PATH '$.comment_author', uid VARCHAR(128) PATH '$.comment_author_uid',
        c   TEXT PATH '$.comment_content',        `like_count` INT PATH '$.comment_like_count'
    )) c
LEFT JOIN JSON_TABLE(
    j.result, '$.results[*]'
    COLUMNS (
        rcid VARCHAR(64) PATH '$.comment_id',       passed BOOLEAN PATH '$.passed',
        is_car_owner BOOLEAN PATH '$.is_car_owner', has_purchase_intent BOOLEAN PATH '$.has_purchase_intent',
        analysis TEXT PATH '$.analysis'
    )) r ON c.cid = r.rcid
WHERE j.job_type = 'comment_screening' AND j.status = 'success'
```

以下记为 **BASE**。每次连接额外执行 `SET SESSION TRANSACTION READ ONLY`（账号本身只授 SELECT，双保险）。

**① 数据覆盖与质量概览**（`data_coverage` → `count_comments`）

```sql
SELECT COUNT(*) AS c FROM ( BASE
    AND j.created_at >= :start_time AND j.created_at <= :end_time
    AND (c.vt LIKE '%#坦克300%')
) AS x
```

对象匹配用 `#标签` 而非标题子串（避坑：近 30 天含「坦克300」的标题命中 74,922 条，实际带 `#坦克300` 标签的只有 21,179 条，其余是「兰德酷路泽vs坦克300」这类竞品对比噪音）。
```json
{"result": {"comment_count": 21179, "datasource": "api_job"}, "sample_size": 21179,
 "bias_note": "统计基于 api_job 成功作业及其展开评论，未做全量向量化主题聚类。"}
```

**② 当前周期声量时间趋势**（`volume_trend` → `time_series`，按天分桶）

```sql
SELECT DATE(j.created_at) AS d, COUNT(*) AS n
FROM api_job j
CROSS JOIN JSON_TABLE(j.request_payload, '$.comments[*]'
    COLUMNS (cid VARCHAR(64) PATH '$.comment_id', vt TEXT PATH '$.video_title')) c
WHERE j.job_type='comment_screening' AND j.status='success'
  AND j.created_at >= :start_time AND j.created_at <= :end_time
  AND (c.vt LIKE '%#坦克300%')
GROUP BY d ORDER BY DATE(j.created_at)
```

返回后按桶补齐缺失日期（`_aggregate_time_series`）。
```json
{"result": {"start": "2026-08-05", "end": "2026-09-04", "bucket": "day",
  "buckets": [{"start": "2026-08-05", "count": 700}, {"start": "2026-08-06", "count": 640}],
  "total": 21179},
 "bias_note": "时间锚点取作业 created_at(UTC)，非评论发布时间。"}
```

**③ 当前周期 vs 对比周期**（`period_comparison` → `count_comments` 两次）
遍历：BASE 追加 `当前窗口` 与 `等长的前一窗口`（`2026-07-06 ~ 2026-08-04`）各计数一次。
```json
{"result": {"current": 21179, "previous": 18540, "change_rate": 14.2,
  "note": "对比周期为当前窗口等长的前一周期"},
 "bias_note": "变化率基于作业 created_at 分桶；prev=0 时 change_rate 为 null。"}
```

**④ 候选子主题**（`topic_frequency_tool` → `topic_frequency`）
进 Python 后按 `#([^#\s]+)` 正则解析每个 `video_title` 的话题标签并计数，去掉对象自身标签后取 Top 50：
```sql
SELECT c.vt AS video_title
FROM api_job j
CROSS JOIN JSON_TABLE(j.request_payload, '$.comments[*]'
    COLUMNS (vt TEXT PATH '$.video_title')) c
WHERE j.job_type='comment_screening' AND j.status='success'
  AND j.created_at >= :start_time AND j.created_at <= :end_time
  AND (c.vt LIKE '%#坦克300%')
```
```json
{"result": {"topics": [{"topic": "油耗", "comment_count": 40}, {"topic": "保值率", "comment_count": 35}]},
 "bias_note": "主题来自视频标题的话题标签，非全量评论分词，可能遗漏隐含表达。"}
```

**⑤ 头部来源**（`top_sources` → `top_videos`，按 `video_title` 聚合）
一个 `api_job` 只是同视频 ≤50 条评论的一个批次，按 `job_id` 聚合无意义（全部封顶 50），故按 `video_title` 聚合，
取该标题下最小 `job_id` 作为代表性溯源 ID：
```sql
SELECT MIN(j.id) AS job_id, c.vt AS video_title, COUNT(*) AS comment_count,
       COALESCE(SUM(c.like_count),0) AS like_sum, COUNT(DISTINCT j.id) AS job_count
FROM api_job j
CROSS JOIN JSON_TABLE(j.request_payload, '$.comments[*]'
    COLUMNS (cid VARCHAR(64) PATH '$.comment_id', vt TEXT PATH '$.video_title',
             `like_count` INT PATH '$.comment_like_count')) c
WHERE j.job_type='comment_screening' AND j.status='success'
  AND j.created_at >= :start_time AND j.created_at <= :end_time
  AND (c.vt LIKE '%#坦克300%')
GROUP BY c.vt ORDER BY comment_count DESC LIMIT :lim
```
```json
{"result": {"videos": [{"job_id": "123", "video_title": "真实测评坦克300油耗",
  "comment_count": 1510, "like_sum": 890, "job_count": 32}]},
 "bias_note": "按评论数排序，代表声量集中来源；不代表全部传播渠道。"}
```

**⑥ 分层抽样评论**（`sample_comments` → `fetch_comments`，`order_by="random"`，限 30 条）
```sql
SELECT * FROM ( BASE
    AND j.created_at >= :start_time AND j.created_at <= :end_time
    AND (c.vt LIKE '%#坦克300%')
) AS expanded ORDER BY RAND() LIMIT :lim
```
```json
{"result": {"comments": [{"comment_id": "c1", "content": "这车市区油耗真不低",
  "video_title": "真实测评坦克300油耗", "job_id": "123", "comment_like_count": 208,
  "passed": true, "is_car_owner": true, "has_purchase_intent": false}]},
 "bias_note": "随机抽样 30 条代表评论，非全量，存在抽样偏差。"}
```

**⑦ 下钻高互动证据**（`drill_evidence` → `fetch_comments`，`order_by="likes"`）
同上但 `ORDER BY comment_like_count DESC LIMIT :lim`，偏向高关注样本。

**⑧ 对象比较**（`object_compare` → `count_comments`）
V0.2 基线不传参照对象（`other_tags=None`），只返回对象自身声量：
```json
{"result": {"object_count": 21179, "other_count": null, "compared": false, "other_tags": null},
 "bias_note": "未提供参照对象标签（other_tags），本次未做对比，仅返回对象自身声量。"}
```

8 个工具的结果与命中证据统一进入本次运行的 `EvidenceStore`（评论/视频/统计三类），
并累积为 `AnalysisBundle`（含 `scope/overall/themes/sources/samples/stats`），作为 LLM 的输入。

### 2.4 报告生成：1 次真实 LLM 调用

- **输入**：`build_report_messages(bundle, intent)` 生成的 `[system, user]` 两条消息。
- **system prompt（本文实际内容）**：

```
你是一名汽车舆情分析师。基于用户提供的结构化统计数据与抽样评论，生成一份《舆情策略包》。
要求：
1. 事实与判断分离：数字结论必须引用给定统计数据；解释性判断需说明依据；证据不足以支撑的结论要标记为【待验证假设】。
2. 只能引用输入中出现的 comment_id 与 job_id（或 video_title），不得编造输入中不存在的 ID。
3. 输出严格为 JSON 对象，字段如下：
   scope(数据覆盖范围与样本说明),
   overall(总体声量及变化),
   themes[{theme, heat_up, refs:[comment_id]}],
   sources[{job_id, video_title, comment_count}],
   risk_opportunity[{type: risk|opportunity, title, reason, refs}],
   evidence_gaps[{gap, why}],
   assumptions[{assumption, how_to_verify}],
   actions[{action, priority, target}],
   metrics[{metric, target}]
```

- **user 消息**：固定引导语 + 序列化后的上下文 JSON（`json.dumps(..., ensure_ascii=False)`）。`stats` 做了裁剪，每项只保留 `name/result/sample_size/bias_note`，丢弃 `params/evidence_ids/time_range`（对模型无直接帮助、徒增 token）。示例：

```
以下是本次舆情分析的结构化统计与抽样评论，请据此生成舆情策略包：

{"intent": {"object": "坦克300", "goal_type": "pulse",
  "time_range": {"start": "2026-08-05", "end": "2026-09-04"},
  "raw_text": "分析坦克300近期的舆情变化"},
 "scope": {"comment_count": 21179, "time_range": {"start": "2026-08-05", "end": "2026-09-04"},
  "datasource": "api_job", "bias_note": "统计基于 api_job 成功作业及其展开评论，未做全量向量化主题聚类。"},
 "overall": {"current": 21179, "previous": 18540, "change_rate": 14.2, "bias_note": "..."},
 "themes": {"topics": [{"topic": "油耗", "comment_count": 40}, {"topic": "保值率", "comment_count": 35}],
  "bias_note": "主题来自视频标题的话题标签，非全量评论分词。"},
 "sources": {"videos": [{"job_id": "123", "video_title": "真实测评坦克300油耗",
  "comment_count": 1510, "like_sum": 890, "job_count": 32}], "bias_note": "..."},
 "samples": [{"comment_id": "c1", "content": "这车市区油耗真不低",
  "video_title": "真实测评坦克300油耗", "job_id": "123"}],
 "stats": [{"name": "data_coverage", "result": {"comment_count": 21179, "datasource": "api_job"},
  "sample_size": 21179, "bias_note": "..."}]}
```

- **调用参数**（`provider.chat_json`）：`response_format={"type": "json_object"}`；**不设 `max_tokens`**（deepseek 系推理模型会把输出预算先用在与 `reasoning_content` 相关的部分，人为设上限过低会在推理阶段耗尽、最终答案为空 `finish_reason=length`，输出上限交由模型自身决定）；超时取 `max(300s, provider.timeout)`。
- **输出**：解析后的报告 JSON（9 字段契约见 §3）与 `LLMResult`（model、prompt/completion/total tokens、latency_ms）。

### 2.5 报告引用校验 —— 无 LLM、无 DB（对照内存证据库）

- **输入**：报告 JSON + 本次运行的 `EvidenceStore`。
- **输出**：`(cleaned_report, validation)`。
- **核心逻辑**（`validate_report` / `_sanitize`，递归遍历一次完成「统计 + 清理」）：
  - `comment_id` 命名空间：`refs` 列表、字典里的 `comment_id` 字段，凡 `str(x)` 不在证据库 `comment_ids` 中的**剔除**并记入 `rejected`；
  - `job_id` 命名空间：列表元素带 `job_id` 的，凡 `str(job_id)` 不在 `video_ids` 中的整项丢弃；
  - 所有比较前 `str()` 归一化，避免 int/str 类型 ID 漏判。
- **示例**：

```json
{"total_refs": 5, "valid_refs": 4, "rejected_refs": ["c999"],
 "rejected_count": 1, "notes": ["剔除 1 条无法在证据库回查的引用"]}
```

### 2.6 落库与返回 —— SQLite 写

- **输入**：`cleaned_report`、`validation`、`evidence_list`、`meta`。
- **输出**：`result`（存为 `task.result`），状态置 `success`；`status=failed` 时保留错误信息。

```json
{
  "report": { ...9 字段契约... },
  "validation": {"total_refs": 5, "valid_refs": 4, "rejected_refs": ["c999"], "rejected_count": 1,
                 "notes": ["剔除 1 条无法在证据库回查的引用"]},
  "evidence": [ {"evidence_id": "...", "kind": "comment", "comment_id": "c1", "content": "..."} ],
  "meta": {"model": "deepseek-v4-flash-0731", "prompt_tokens": 3200,
           "completion_tokens": 1800, "total_tokens": 5000, "latency_ms": 42000},
  "progress": {"stage": "done", "tool_calls": 8, "report_success": true}
}
```

---

## 3. 报告输出协议（9 字段契约）

| 字段组 | 内容 | 证据要求 |
|---|---|---|
| `scope` | 数据覆盖范围与样本说明 | 引用统计、样本量、时间窗 |
| `overall` | 总体声量及变化 | 当前/对比总量与变化率 |
| `themes` | 核心主题、升温问题 | 每主题绑定代表评论 `comment_id` |
| `sources` | 代表性传播来源 | 绑定 `job_id` / `video_title` |
| `risk_opportunity` | 风险、机会及其可能原因 | 结论绑定证据 |
| `evidence_gaps` | 支撑证据、反例、不确定性 | 无需绑定、明示缺口 |
| `assumptions` | 待验证假设 | 明示为假设，非事实 |
| `actions` | 监测、澄清、回应、继续调查 | 指向具体证据/主题 |
| `metrics` | 后续验证指标 | 量化、可回查 |

校验后的示例输出：

```json
{
  "scope": {"comment_count": 21179, "time_range": {"start": "2026-08-05", "end": "2026-09-04"},
            "sample_size": 30, "bias_note": "随机抽样 30 条代表评论，非全量。"},
  "overall": {"current": 21179, "previous": 18540, "change_rate": 14.2},
  "themes": [{"theme": "市区油耗偏高", "heat_up": "升温", "refs": ["c1"]}],
  "sources": [{"job_id": "123", "video_title": "真实测评坦克300油耗", "comment_count": 1510}],
  "risk_opportunity": [{"type": "risk", "title": "油耗口碑可能抑制转化",
                        "reason": "抽样中多条高互动评论提及市区油耗", "refs": ["c1", "c2"]}],
  "evidence_gaps": [{"gap": "缺少真实购车意图样本", "why": "抽样多为车主分享，鲜有明确对比意向"}],
  "assumptions": [{"assumption": "油耗话题对销量影响有限", "how_to_verify": "对比近一季度转化数据"}],
  "actions": [{"action": "准备油耗场景实测内容", "priority": "high", "target": "油耗子主题"}],
  "metrics": [{"metric": "油耗相关评论占比", "target": "低于 15%"}]
}
```

---

## 4. 关键口径与已知问题

- **时间锚点**：统一用 `api_job.created_at`（UTC），不用评论自带时间——实测后者最早到 2023、最晚晚于作业创建时间，不可靠。
- **对象匹配**：`video_title LIKE '%#对象%'`（前导 `#` 紧贴对象；标签组内空格/换行分隔，尾部无 `#`，故不能要求尾 `#`）。子话题如 `#坦克300油耗` 同样命中，属预期。
- **来源身份**：按 `video_title` 聚合，`job_id` 取该标题下最小值作代表性溯源 ID。
- **已知问题（非阻断）**：`topic_frequency` 的 SQL 无 `LIMIT`（TopN 在 Python 侧截断，V1）；`created_at` 为 UTC，前端展示未做时区换算（V5）。
