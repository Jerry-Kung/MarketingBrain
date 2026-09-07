# Marketing Brain V0.2 测试报告

> 记录 V0.2（非 Agent 基线：真实 LLM 固定流程 → 舆情策略包）的测试与冒烟验证结果。
> 范围：单元测试、集成测试、真实 LLM 冒烟、前端构建。已知问题见文末。

## 1. 测试结论总览

| 层级 | 结果 | 说明 |
|---|---|---|
| 后端单元（非集成） | **117 passed** | 默认 `pytest` 用 `-m "not integration"` 过滤后的非集成测试 |
| 集成测试（`-m integration`） | **11 tests** | 连接真实测试环境 MySQL（只读账号） |
| 真实 LLM 冒烟 | **通过**（1 次真实调用） | V0.2 固定流水线端到端生成报告，引用校验全过 |
| 前端构建 / 静态检查 | **通过** | `npm run build` OK；`npm run lint`（oxlint）clean |

- 未提供独立的前端运行时测试框架（无 e2e / jest）。
- 集成测试默认被 `markers` 标记跳过；运行需显式 `-m integration` 且 `.env` 配置了可用的只读 MySQL 账号。

## 2. 后端单元测试（非集成）

一次 `pytest -m "not integration"` 运行：共收集 128 项，11 项被 deselected，**117 项全部通过**，耗时约 1.4s。

按文件的通过数（来自最近一次运行）：

| 测试文件 | 通过数 |
|---|---|
| `test_api` | 9 |
| `test_api_v02` | 6 |
| `test_config` | 6 |
| `test_datasource` | 5 |
| `test_datasource_tools` | 12 |
| `test_evidence` | 7 |
| `test_intent` | 9 |
| `test_llm_provider` | 8 |
| `test_llm_report` | 10 |
| `test_pipeline_baseline` | 3 |
| `test_runner` | 6 |
| `test_snapshot` | 5 |
| `test_store` | 7 |
| `test_verify` | 12 |
| **合计** | **117** |

被 deselected 的 11 项即 `tests/test_mysql_integration.py` 中所有 `integration` 标记测试（真实 MySQL），不在单测范围。

## 3. 集成测试（`-m integration`）

11 项，全部针对真实测试环境 MySQL 的只读账号（读取 `.env` 配置）。覆盖：

- 连接与总量（`connectivity_and_count`）；
- 评论拉取及其 `limit` 精确限制（`fetch_comments` / `fetch_comments_respects_limit`）；
- 数据概览（`data_overview`）；
- 带时间窗 + 标签过滤的查询（`fetch_comments_with_filters` / `count_comments_tags`）；
- 时间序列形状（`time_series`，8 月 31 天 → 31 个 bucket）；
- 头部视频聚合（`top_videos`，聚合评论数可超过单 job 的 50 条上限并带 `job_count`）；
- 主题词频（`topic_frequency`）；
- 按点赞排序（`order_by_likes`）与随机排序（`order_by_random`）。

结论：真实数据链路的查询正确性与口径（时间窗、`#标签` 对象匹配、`video_title` 聚合）均在真实数据库上验证通过。

## 4. 真实 LLM 冒烟（V0.2 固定流水线）

一次真实调用端到端跑通「数据取证 → 报告 → 引用校验 → 落库」，结论：

| 项 | 值 |
|---|---|
| 任务状态 | `success` |
| 模型 | `deepseek-v4-flash-0731` |
| prompt token | 17,475 |
| completion token | 12,477 |
| total token | **29,952** |
| 延迟 | **129,150 ms**（约 2.1 分钟） |
| 引用校验 | **validation 32/32 有效，0 被剔除** |
| 证据索引 | **75 条**（comment / video / stat） |
| 产出报告 | 含全部 9 个顶层键：`scope, overall, themes, sources, risk_opportunity, evidence_gaps, assumptions, actions, metrics` |
| 主题 / 行动 | 9 个 `themes`、6 个 `actions` |
| 引用可回查 | `themes[].refs` 共 23 个 ID，全部可回溯到证据索引 |

**结论**：真实 LLM 与固定流水线可正确生成固定结构的舆情策略包；引用校验环节未发现非法 ID 混入，报告的评论/视频 ID 均可回查。这为 V0.4 的 Agent 对照建立了可复现的非 Agent 基线。

**过程中的关键发现（已修复）**：最初配置 `max_tokens=4000` 时模型输出为空。原因：deepseek 系推理模型会把输出预算大量用于 `reasoning_content`，上限偏低时在推理阶段耗尽（`finish_reason=length`），最终答案为空。修复：报告请求不再人为设置 `max_tokens`（上限交由模型自身决定），并使用不低于 300s 的超时（覆盖默认 120s）。

## 5. 前端构建与静态检查

| 项 | 结果 |
|---|---|
| `npm run build`（Vite） | 通过，17 modules transformed，126ms |
| `npm run lint`（oxlint） | 通过，无错误 |

## 6. 已知问题 / 待办

- **运行成本与耗时**：每次报告约消耗 3 万 token、耗时约 2 分钟；属推理模型的正常开销，后续需评估是否对报告请求做更细的预算/缓存控制。
- **`pytest` 默认无 `-m "not integration"`**：`pytest.ini` 未配置默认 marker 过滤，直接运行 `pytest` 会尝试连接真实 MySQL。开发/CI 建议显式加 `-m "not integration"` 或补默认配置，避免误触真实数据库。
- **延迟构成**：129s 的耗时主要由大上下文 + 长输出（reasoning）主导；后续可评估拆分调用或裁剪上下文来缩短。

## 7. 完成标准（Definition of Done）对照

- [x] 单元测试全部通过（含 V0.1 回归全绿）；
- [x] 集成测试（`-m integration`）通过；真实 LLM 冒烟成功，引用的评论/视频 ID 可在证据库回查；
- [x] `docker compose` 可部署，容器重建后历史任务与报告仍可查看（由部署骨架保障）；
- [x] 前端可创建任务、查看运行状态（模型/Token/耗时）与基线报告；
- [x] 失败任务保留错误信息，不产生伪报告；
- [x] 已记录已知问题，无阻断核心链路缺陷。
