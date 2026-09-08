"""V0.4 Agent 后台/同步运行器。

与 app/workflow/runner.py、app/pipeline/runner.py 结构保持一致：
try/except 包全部、终态守卫、事件追加、出错 mark_failed。
"""
import threading
import traceback


def run_agent_sync(task_id, *, task_repo, event_repo, datasource,
                   llm_provider, settings, max_subtasks=None, max_tool_calls=None):
    """同步执行一次 Agent 任务，返回 result。出错则标记 failed 并保留错误。

    Args:
        task_id: 任务 ID。
        task_repo: TaskRepository。
        event_repo: EventRepository。
        datasource: 数据源适配器。
        llm_provider: LLM Provider（Supervisor/Investigator/Reviewer 共用）。
        settings: Settings。
        max_subtasks: 请求级最大子任务数（透传给 Orchestrator）。
        max_tool_calls: 请求级最大工具调用数（透传给 Orchestrator）。

    Returns:
        dict: Orchestrator.run 返回的结果；终态守卫 / 任务不存在 / 异常时返回对应错误字典。
    """
    try:
        task = task_repo.get_task(task_id)

        # 终态守卫：已完成/已失败的任务不得重跑（不重置状态、不覆盖已存结果）
        if task is not None and task.status in {"success", "failed"}:
            return {"already": task.status}
        if task is None:
            # 任务不存在：无法标记 failed，返回错误字典，不向 try 外抛出
            return {"error": "task not found"}

        task_repo.update_status(task_id, "running")
        event_repo.append_event(task_id, "task_started", {"task_id": task_id})

        # 重建快照
        from app.snapshot.snapshot import LogicalSnapshot
        snapshot = LogicalSnapshot.from_dict(task.snapshot)

        # 还原任务意图（必须为 AnalysisIntent 对象）
        from app.understanding.intent import AnalysisIntent
        intent = AnalysisIntent.from_dict(task.parsed_intent)

        from app.store.evidence import EvidenceStore
        evidence_store = EvidenceStore(task_id)

        from app.agent.orchestrator import Orchestrator
        orch = Orchestrator(
            llm_provider, datasource, snapshot, evidence_store,
            event_repo, task_repo, settings,
        )
        result = orch.run(
            task_id, intent,
            max_subtasks=max_subtasks, max_tool_calls=max_tool_calls,
        )

        # 持久化结果（save_result 同时将状态置为 success）
        task_repo.save_result(task_id, result)
        event_repo.append_event(task_id, "task_finished", {"status": "success"})

        return result
    except Exception as e:
        # mark_failed 同时写 error 字段并将状态置为 failed
        task_repo.mark_failed(task_id, str(e))
        event_repo.append_event(task_id, "task_failed", {
            "error": str(e), "traceback": traceback.format_exc(limit=5),
        })
        return {"error": str(e)}


def start_agent_background(task_id, *, task_repo, event_repo, datasource,
                           llm_provider, settings, max_subtasks=None,
                           max_tool_calls=None) -> threading.Thread:
    """新线程执行 Agent 任务（不引入 Celery/消息队列）。"""

    def _worker():
        run_agent_sync(
            task_id, task_repo=task_repo, event_repo=event_repo,
            datasource=datasource, llm_provider=llm_provider, settings=settings,
            max_subtasks=max_subtasks, max_tool_calls=max_tool_calls,
        )

    t = threading.Thread(target=_worker, daemon=True, name=f"agent-{task_id}")
    t.start()
    return t
