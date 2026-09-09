"""V0.6 一次性 LLM 报告流水线（种子基线）。

与 V0.2 固定流程共用 BaselinePipeline 的确定性取数，但报告由**单次** LLM
调用生成（不分阶段、无子 Agent 下钻、不校验），构成「非 Agent 非固定」的
对照基线，用于 V0.6 三模式（一次性 / 固定流程 / 受控 Agent）对比评测。
"""
import threading

from app.pipeline.baseline import BaselinePipeline
from app.llm.report import generate_strategy_pack


def run_oneshot_sync(task_id, *, task_repo, event_repo, datasource,
                     llm_provider, settings=None):
    """同步执行一次 oneshot 任务，返回 result。出错则标记 failed。"""
    try:
        task = task_repo.get_task(task_id)

        # 终态守卫：已完成/已失败的任务不得重跑（不重置状态、不覆盖已存结果）
        if task is not None and task.status in {"success", "failed"}:
            return {"already": task.status}
        if task is None:
            # 任务不存在：无法标记 failed，返回错误字典，不向 try 外抛出
            return {"error": "task not found"}

        from app.snapshot.snapshot import LogicalSnapshot
        from app.store.evidence import EvidenceStore
        snapshot = LogicalSnapshot.from_dict(task.snapshot)

        task_repo.update_status(task_id, "running")
        event_repo.append_event(task_id, "task_started", {"task_id": task_id})

        evidence_store = EvidenceStore(task_id)
        pipeline = BaselinePipeline(datasource, snapshot, evidence_store)
        event_repo.append_event(task_id, "pipeline_start", {"stage": "oneshot"})
        bundle = pipeline.run()

        event_repo.append_event(task_id, "report_start", {})
        report, llm_result = generate_strategy_pack(bundle, llm_provider, intent=task.parsed_intent)
        event_repo.append_event(task_id, "report_done", {"usage": llm_result.usage.to_dict()})

        evidence_list = evidence_store.to_dicts()
        result = {
            "report": report,
            "evidence": evidence_list,
            "meta": {
                "model": llm_result.usage.model,
                "prompt_tokens": llm_result.usage.prompt_tokens,
                "completion_tokens": llm_result.usage.completion_tokens,
                "total_tokens": llm_result.usage.total_tokens,
                "latency_ms": llm_result.usage.latency_ms,
            },
            "mode": "oneshot",
            "progress": {"stage": "done", "tool_calls": 0, "report_success": True},
        }
        task_repo.save_result(task_id, result)
        event_repo.append_event(task_id, "task_finished", {"status": "success"})
        return result
    except Exception as e:
        task_repo.mark_failed(task_id, str(e))
        event_repo.append_event(task_id, "task_failed", {"error": str(e)})
        return {"error": str(e)}


def start_oneshot_background(*, task_id, task_repo, event_repo, datasource,
                             llm_provider, settings=None):
    """在新线程里执行 oneshot 任务（V0 不引入 Celery）。"""
    def _worker():
        run_oneshot_sync(
            task_id, task_repo=task_repo, event_repo=event_repo,
            datasource=datasource, llm_provider=llm_provider, settings=settings,
        )
    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return t
