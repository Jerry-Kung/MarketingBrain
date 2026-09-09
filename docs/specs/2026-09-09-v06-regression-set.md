# V0.6 最小回归任务集（模板）

> 用途：选定 5—10 个真实历史任务，作为三模式对照与最终验收的统一输入。
> 真实任务输入、预期证据点由现场从真实舆情数据中选定后回填；本文件先给结构。

## 任务清单

| 序号 | 分析对象 | 时间窗口 | 案例类型 | 预期证据点 | 验收通道 |
|---|---|---|---|---|---|
| 1 | （回填） | 近30天 | 异常升温 | 高温视频/评论ID | （回填） |
| 2 | （回填） | 近30天 | 数据不足 | 覆盖率低 | ... |
| ... | ... | ... | ... | ... | ... |
| 5—10 | ... | ... | ... | ... | ... |

## 跑取命令

三模式没有共用的 `mode` 参数一把切换：`oneshot` 显式指定即可；固定流程（V0.2 基线）与受控 Agent（V0.4）由 `ENABLE_AGENT_ENGINE` / `ENABLE_WORKFLOW_ENGINE` 两个开关路由决定，不存在 `mode="baseline"` 这个取值。

```bash
# 模式一：一次性 LLM 报告（V0.6 新增，显式指定 mode 即可，不受开关影响）
curl -X POST http://<host>/api/tasks -d '{"raw_input":"...","mode":"oneshot"}' -H 'Content-Type: application/json'

# 模式二：固定流程（V0.2 基线）——需先把两个引擎开关都关掉，再用默认创建（不传 mode）
# .env 或环境变量：ENABLE_AGENT_ENGINE=false ENABLE_WORKFLOW_ENGINE=false
curl -X POST http://<host>/api/tasks -d '{"raw_input":"..."}' -H 'Content-Type: application/json'

# 模式三：受控 Agent（V0.4，默认路由）——保持两个开关为 true（默认值），默认创建或显式 mode="agent" 均可
curl -X POST http://<host>/api/tasks -d '{"raw_input":"..."}' -H 'Content-Type: application/json'
curl -X POST http://<host>/api/tasks -d '{"raw_input":"...","mode":"agent"}' -H 'Content-Type: application/json'
```

> 注：固定流程与受控 Agent 共用 `POST /api/tasks`，走向由 `ENABLE_AGENT_ENGINE`/`ENABLE_WORKFLOW_ENGINE` 两个配置开关路由决定（`app/api/routes.py`），不是 `mode` 取值。对照固定流程时需临时关闭两个开关，跑完后记得改回默认值，避免影响其它正常任务的路由。
