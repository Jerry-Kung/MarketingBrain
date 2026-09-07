"""与 V0.2 BaselinePipeline 并行的 V0.3 工作流后台运行器。"""
import threading


def run_workflow_sync(
    task_id, *, skill_name, task_repo, event_repo, datasource, llm_provider, settings
):
    """同步执行工作流任务，返回 result。出错则标记 failed 并保留错误。"""
    try:
        task = task_repo.get_task(task_id)

        # 终态守卫
        if task is not None and task.status in {"success", "failed"}:
            return {"already": task.status}
        if task is None:
            return {"error": "task not found"}

        task_repo.update_status(task_id, "running")
        event_repo.append_event(task_id, "task_started", {"task_id": task_id})

        # 加载 Skill
        from app.skill.loader import SkillLoader
        loader = SkillLoader(skills_dir=settings.skills_dir)
        skill = loader.load(skill_name)

        # 重建快照
        from app.snapshot.snapshot import LogicalSnapshot
        snapshot = LogicalSnapshot.from_dict(task.snapshot)

        from app.store.evidence import EvidenceStore
        evidence_store = EvidenceStore(task_id)

        from app.workflow.engine import WorkflowEngine
        engine = WorkflowEngine(
            skill, datasource, snapshot, evidence_store,
            llm_provider, task_repo, event_repo,
            stage_timeout=settings.workflow_stage_timeout,
        )

        result = engine.run(task_id)

        # 持久化结果（save_result 同时将状态置为 success）
        task_repo.save_result(task_id, result)
        event_repo.append_event(
            task_id, "task_finished", {"task_id": task_id, "status": "success"}
        )

        return result

    except Exception as e:
        err_msg = f"{type(e).__name__}: {e}"
        # mark_failed 同时写 error 字段并将状态置为 failed
        task_repo.mark_failed(task_id, err_msg)
        event_repo.append_event(
            task_id, "task_failed", {"task_id": task_id, "error": err_msg}
        )
        return {"error": err_msg}


def start_workflow_background(
    task_id, skill_name, *, task_repo, event_repo, datasource, llm_provider, settings
):
    """后台线程启动工作流任务。"""

    def _worker():
        run_workflow_sync(
            task_id,
            skill_name=skill_name,
            task_repo=task_repo,
            event_repo=event_repo,
            datasource=datasource,
            llm_provider=llm_provider,
            settings=settings,
        )

    thread = threading.Thread(target=_worker, daemon=True, name=f"workflow-{task_id}")
    thread.start()
    return thread
