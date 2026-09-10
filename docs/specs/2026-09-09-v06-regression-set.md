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

三模式没有共用的 `mode` 参数一把切换：`oneshot` 是唯一被接受的 `mode` 取值；固定流程（V0.2 基线）与受控 Agent（V0.4）由 `ENABLE_AGENT_ENGINE` / `ENABLE_WORKFLOW_ENGINE` 两个开关路由决定。传入 `oneshot` 之外的任何 `mode`（如 `"agent"` / `"workflow"` / `"baseline"`）会被接口拒绝并返回 **422**，不会静默落到默认分支。

```bash
# 模式一：一次性 LLM 报告（V0.6 新增，显式指定 mode 即可，不受开关影响）
curl -X POST http://<host>/api/tasks -d '{"raw_input":"...","mode":"oneshot"}' -H 'Content-Type: application/json'

# 模式二：固定流程（V0.2 基线）——需先把两个引擎开关都关掉，再用默认创建（不传 mode）
# .env 或环境变量：ENABLE_AGENT_ENGINE=false ENABLE_WORKFLOW_ENGINE=false
curl -X POST http://<host>/api/tasks -d '{"raw_input":"..."}' -H 'Content-Type: application/json'

# 模式三：受控 Agent（V0.4，默认路由）——保持两个开关为 true（默认值），默认创建即可，不传 mode
curl -X POST http://<host>/api/tasks -d '{"raw_input":"..."}' -H 'Content-Type: application/json'
```

> 注：固定流程与受控 Agent 共用 `POST /api/tasks`，走向由 `ENABLE_AGENT_ENGINE`/`ENABLE_WORKFLOW_ENGINE` 两个配置开关路由决定（`app/api/routes.py`），不是 `mode` 取值。对照固定流程时需临时关闭两个开关，跑完后记得改回默认值，避免影响其它正常任务的路由。

> **改开关必须重启服务**：配置只在 `create_app()` 时加载一次，并被请求处理函数的闭包捕获，改完 `.env` 里的 `ENABLE_AGENT_ENGINE`/`ENABLE_WORKFLOW_ENGINE` 后不重启服务，新值不会生效（跑出来的仍是上一模式，会造成对照结果误标）。
