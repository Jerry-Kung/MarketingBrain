"""任务执行器。

后台线程驱动固定流水线：取证 → 生成报告 → 引用校验 → 落库 → 标记成功/失败。
全程写追加式事件。失败保留错误，不生成伪报告。
"""
import threading
import traceback


def run_task_sync(task_id, *, task_repo, event_repo, datasource,
                  llm_provider, settings=None):
    """同步执行一次任务，返回 result。出错则标记 failed 并保留错误。"""
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

        from app.store.evidence import EvidenceStore
        evidence_store = EvidenceStore(task_id)

        from app.pipeline.baseline import BaselinePipeline
        pipeline = BaselinePipeline(datasource, snapshot, evidence_store)
        event_repo.append_event(task_id, "pipeline_start", {"stage": "baseline"})
        bundle = pipeline.run()
        for st in bundle.stats:
            event_repo.append_event(task_id, "tool_call",
                                    {"tool": st["name"],
                                     "sample_size": st["sample_size"]})

        event_repo.append_event(task_id, "report_start", {})
        from app.llm.report import generate_strategy_pack
        # Controller ruling P7：透传任务解析后的意图
        report, llm_result = generate_strategy_pack(
            bundle, llm_provider, intent=task.parsed_intent,
        )
        event_repo.append_event(task_id, "report_done", {
            "usage": llm_result.usage.to_dict(),
        })

        event_repo.append_event(task_id, "validate_start", {})
        from app.pipeline.verify import validate_report
        cleaned, validation = validate_report(report, evidence_store)
        event_repo.append_event(task_id, "validate_done", validation)

        evidence_list = evidence_store.to_dicts()
        result = {
            "report": cleaned,
            "validation": validation,
            "evidence": evidence_list,
            "meta": {
                "model": llm_result.usage.model,
                "prompt_tokens": llm_result.usage.prompt_tokens,
                "completion_tokens": llm_result.usage.completion_tokens,
                "total_tokens": llm_result.usage.total_tokens,
                "latency_ms": llm_result.usage.latency_ms,
            },
            # Controller ruling P8：前端可用的粗粒度进度摘要
            "progress": {
                "stage": "done",
                "tool_calls": len(bundle.stats),
                "report_success": True,
            },
        }
        task_repo.save_result(task_id, result)
        event_repo.append_event(task_id, "task_finished", {"status": "success"})
        return result
    except Exception as e:
        task_repo.mark_failed(task_id, str(e))
        event_repo.append_event(task_id, "task_failed", {
            "error": str(e), "traceback": traceback.format_exc(limit=5),
        })
        return {"error": str(e)}


def start_task_background(*, task_id, task_repo, event_repo, datasource,
                          llm_provider, settings=None):
    """在新线程里执行任务（V0 不引入 Celery/消息队列）。"""
    def _worker():
        run_task_sync(
            task_id, task_repo=task_repo, event_repo=event_repo,
            datasource=datasource, llm_provider=llm_provider, settings=settings,
        )
    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return t
