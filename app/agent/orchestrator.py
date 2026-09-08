"""Orchestrator：编排 Supervisor -> Investigator[] -> Reviewer -> 综合报告。

主流程固定（确定性骨架），探索由子 Agent 在预算内完成。预算/停止条件/评审门禁
属于本模块，普通子 Agent 无法绕过。
"""
import time

from app.agent.budgets import AgentBudget, BudgetCounter
from app.agent.investigator import Investigator
from app.agent.protocols import InvestigationCard, STOP_REASON_BUDGET
from app.agent.reviewer import Reviewer
from app.agent.supervisor import Supervisor
from app.agent.tools_spec import TOOL_WHITELIST


class Orchestrator:
    """V0.4 任务编排。"""

    def __init__(self, llm_provider, datasource, snapshot, evidence_store,
                 event_repo, task_repo, settings):
        self.llm_provider = llm_provider
        self.datasource = datasource
        self.snapshot = snapshot
        self.evidence_store = evidence_store
        self.event_repo = event_repo
        self.task_repo = task_repo
        self.settings = settings
        self.supervisor = Supervisor(llm_provider, settings)
        self.reviewer = Reviewer(llm_provider, settings)
        self._started = None

    def run(self, task_id: str, intent, *, max_subtasks=None, max_tool_calls=None) -> dict:
        self._started = time.monotonic()
        self.event_repo.append_event(task_id, "agent_run_start", {"task_id": task_id})

        budget = self._build_budget(max_subtasks, max_tool_calls)
        counter = BudgetCounter(budget)
        cards = self.supervisor.plan(intent, self.evidence_store, budget.subtasks)
        self.event_repo.append_event(task_id, "agent_plan",
                                     {"cards": [c.to_dict() for c in cards], "budget": counter.to_dict()})

        subtask_results = []
        for card in cards:
            if counter.subtask_exceeded():
                self.event_repo.append_event(task_id, "subtask_stop", {"card_id": card.card_id, "stop_reason": STOP_REASON_BUDGET})
                break
            counter.record_subtask()
            inv = self._make_investigator(counter, budget)
            res = inv.run(card, task_id, round_no=1)
            self._register_judgments_and_assumptions(res)
            subtask_results.append(res)

        review = self.reviewer.review(subtask_results, cards, self.evidence_store, task_id)
        self.event_repo.append_event(task_id, "review_result", review.to_dict())

        # 受控补查（至多 1 次）
        if review.verdict == "request_supplement" and not counter.supplement_exceeded():
            if review.supplement_query:
                counter.record_supplement()
                supplement_card = InvestigationCard(
                    card_id=f"supplement-{len(subtask_results)+1}",
                    goal_type="drill", title="补充调查", objective=review.supplement_query,
                    evidence_requirements=["补充证据"], suggested_tools=[], priority=0,
                )
                counter.record_subtask()
                inv = self._make_investigator(counter, budget)
                sup_res = inv.run(supplement_card, task_id, round_no=1)
                self._register_judgments_and_assumptions(sup_res)
                subtask_results.append(sup_res)
                rev2 = self.reviewer.review(subtask_results, cards + [supplement_card], self.evidence_store, task_id)
                review = rev2
                self.event_repo.append_event(task_id, "review_result", rev2.to_dict())

        report = self._build_report(cards, subtask_results, review, counter)
        self.event_repo.append_event(task_id, "task_finished", {"status": "success", "budget": counter.to_dict()})
        # 返回结构：report（三层策略包）+ agent 审计块 + evidence 顶层键。
        # agent 块与 evidence 复用在 _build_report 内已组装的同一份，避免重复构造。
        return {
            "report": report,
            "agent": report["agent"],
            "evidence": report["evidence"],
        }

    def _build_budget(self, max_subtasks, max_tool_calls) -> AgentBudget:
        b = AgentBudget.from_settings(self.settings)
        if max_subtasks is not None:
            b.subtasks = max(1, int(max_subtasks))
        if max_tool_calls is not None:
            b.tool_calls = max(1, int(max_tool_calls))
        return b

    def _make_investigator(self, counter, budget):
        return Investigator(
            self.llm_provider, self.datasource, self.snapshot, self.evidence_store,
            self.event_repo, counter, tool_whitelist=TOOL_WHITELIST,
            max_loops=budget.loops, max_records=self.settings.TOOL_MAX_RECORDS,
            settings=self.settings,
        )

    def _register_judgments_and_assumptions(self, result) -> None:
        """把 Investigator 输出的解释性判断/假设登记到证据库，供三层报告组装。

        Investigator 执行工具只登记 comment/video/stat 证据；judgment 与 assumption
        两类非事实层由 Orchestrator 在此补登，否则三层报告中「解释性判断/待验证假设」
        恒为空。
        """
        for finding in getattr(result, "findings", []) or []:
            if isinstance(finding, dict):
                title = finding.get("title")
                judgment_type = finding.get("judgment_type", "theme")
            else:
                title = finding if isinstance(finding, str) else None
                judgment_type = "theme"
            if not title:
                continue
            self.evidence_store.register_judgment(
                judgment_type=judgment_type, title=str(title),
                evidence_refs=[], source="orchestrator",
            )
        hypothesis = getattr(result, "hypothesis", "") or ""
        if hypothesis:
            self.evidence_store.register_assumption(
                title=hypothesis, rationale=getattr(result, "summary", "") or "",
                source="orchestrator",
            )

    def _build_report(self, cards, subtask_results, review, counter) -> dict:
        evidence_list = self.evidence_store.to_dicts()
        stats = [e for e in evidence_list if e["kind"] == "stat"]
        judgments = [e for e in evidence_list if e["kind"] == "judgment"]
        assumptions = [e for e in evidence_list if e["kind"] == "assumption"]
        videos = [e for e in evidence_list if e["kind"] == "video"]

        themes = [{"title": j["extra"].get("title", ""), "evidence_refs": j["extra"].get("evidence_refs", [])}
                  for j in judgments if j["extra"].get("judgment_type") == "theme"]
        risk_opp = [{"title": j["extra"].get("title", ""), "type": j["extra"].get("judgment_type"),
                     "evidence_refs": j["extra"].get("evidence_refs", [])}
                    for j in judgments if j["extra"].get("judgment_type") in ("risk", "opportunity")]
        gaps = [{"title": a["extra"].get("title", ""), "rationale": a["extra"].get("rationale", "")}
                for a in assumptions]
        sources = [{"video_title": v.get("video_title", ""), "job_id": v.get("job_id", "")} for v in videos]

        return {
            "scope": self._scope_from_stats(stats),
            "overall": self._overall_from_stats(stats),
            "themes": themes,
            "sources": sources,
            "risk_opportunity": risk_opp,
            "evidence_gaps": gaps,
            "actions": [],
            "metrics": [],
            "agent": {
                "cards": [c.to_dict() for c in cards],
                "subtask_results": [r.to_dict() for r in subtask_results],
                "review": review.to_dict(),
                "budget": counter.to_dict(),
            },
            "evidence": evidence_list,
            "meta": {
                "model": getattr(self.llm_provider, "model", None),
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "latency_ms": int((time.monotonic() - self._started) * 1000) if self._started else 0,
            },
        }

    @staticmethod
    def _scope_from_stats(stats) -> dict:
        scope = {}
        for s in stats:
            if s["extra"].get("label") == "comment_count":
                scope["comment_count"] = s["extra"].get("count")
        return scope

    @staticmethod
    def _overall_from_stats(stats) -> dict:
        overall = {}
        for s in stats:
            if s["extra"].get("label") == "trend_total":
                overall["current"] = s["extra"].get("count")
            if s["extra"].get("label") == "comparison":
                overall["previous"] = s["extra"].get("count")
        return overall
