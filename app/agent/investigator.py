"""Investigator 子 Agent：单张调查卡的受控调查 Loop。

通过真实 function calling 从白名单工具中选择调用，依据工具结果（证据摘要）决定
继续下钻或停止。每轮开始前检查预算，超限即停止。工具名与参数必须通过授权与校验。
"""
import json

from app.agent.protocols import (
    InvestigationCard, InvestigatorResult, STOP_REASON_EVIDENCE_SUFFICIENT,
    STOP_REASON_DATA_INSUFFICIENT, STOP_REASON_BUDGET, STOP_REASON_TOOL_FAILURE,
    STOP_REASON_ILLEGAL, VALID_STOP_REASONS,
)
from app.agent.tools_spec import (
    TOOL_JSON_SCHEMAS, TOOL_WHITELIST, validate_and_coerce, ToolSpecError,
)
from app.analysis.registry import get_tool
from app.llm.provider import LLMResult


class Investigator:
    """子 Agent：围绕一张调查卡开展有限探索。"""

    def __init__(self, llm_provider, datasource, snapshot, evidence_store,
                 event_repo, budget_counter, *, tool_whitelist=None,
                 max_loops=None, max_records=None):
        self.llm_provider = llm_provider
        self.datasource = datasource
        self.snapshot = snapshot
        self.evidence_store = evidence_store
        self.event_repo = event_repo
        self.budget_counter = budget_counter
        self.tool_whitelist = tool_whitelist or set(TOOL_WHITELIST)
        self.max_loops = max_loops
        self.max_records = max_records or 500

    def run(self, card: InvestigationCard, task_id: str, round_no: int = 1) -> InvestigatorResult:
        """执行单张调查卡，返回 InvestigatorResult。"""
        self.event_repo.append_event(
            task_id, "subtask_start",
            {"card_id": card.card_id, "title": card.title, "round": round_no,
             "suggested_tools": card.suggested_tools},
        )
        tool_calls_used = 0
        messages = self._build_messages(card, task_id)
        loops = 0
        while True:
            # 每轮前预算检查（硬限制）
            self.budget_counter.record_loop()
            loops += 1
            if self.budget_counter.loop_exceeded() or self.budget_counter.tool_calls_exceeded():
                return self._stop(task_id, card, STOP_REASON_BUDGET, "预算耗尽，停止本轮调查", tool_calls_used)

            for attempt in range(2):  # 非法输出重试 1 次
                result = self._call_llm(messages)
                turn = self._parse_turn(result)
                if turn is not None:
                    break
                if attempt == 0:
                    self.event_repo.append_event(task_id, "illegal_output",
                        {"card_id": card.card_id, "error": "无法解析为合法回合，重试"})
                    continue
                return self._stop(task_id, card, STOP_REASON_ILLEGAL, "LLM 输出非法，停止", tool_calls_used)
            else:
                return self._stop(task_id, card, STOP_REASON_ILLEGAL, "LLM 输出非法，停止", tool_calls_used)

            ttype = turn["type"]
            if ttype == "stop":
                reason = turn.get("stop_reason", STOP_REASON_DATA_INSUFFICIENT)
                if reason not in VALID_STOP_REASONS:
                    reason = STOP_REASON_DATA_INSUFFICIENT
                return self._stop(task_id, card, reason,
                                  turn.get("summary", ""), tool_calls_used,
                                  findings=turn.get("findings", []), hypothesis=turn.get("hypothesis", ""))
            # ttype == "tool_call"
            tool_name = turn.get("tool")
            arguments = turn.get("arguments") or {}
            self.budget_counter.record_tool_call()
            if self.budget_counter.tool_calls_exceeded():
                return self._stop(task_id, card, STOP_REASON_BUDGET, "工具调用预算耗尽", tool_calls_used)
            tool_calls_used += 1
            try:
                tool_summary = self._execute_tool(tool_name, arguments, task_id, card)
            except ToolSpecError:
                self.event_repo.append_event(task_id, "tool_unauthorized",
                    {"card_id": card.card_id, "tool": tool_name, "allowed": sorted(self.tool_whitelist)})
                return self._stop(task_id, card, STOP_REASON_ILLEGAL, "工具参数非法或未授权，停止", tool_calls_used)
            except Exception as e:
                self.event_repo.append_event(task_id, "tool_failure",
                    {"card_id": card.card_id, "tool": tool_name, "error": str(e)})
                return self._stop(task_id, card, STOP_REASON_TOOL_FAILURE, f"工具执行失败: {e}", tool_calls_used)
            messages = self._append_tool_result(messages, tool_name, tool_summary, turn.get("reason", ""))

    def _call_llm(self, messages) -> LLMResult:
        return self.llm_provider.chat(messages, tools=TOOL_JSON_SCHEMAS,
                                      temperature=0.2, max_tokens=2000)

    def _parse_turn(self, result: LLMResult) -> dict | None:
        """从 LLMResult 解析合法回合 dict，或 None。

        优先级：tool_calls（function calling）> content 中的 {"type":"stop"/"tool_call"}。
        """
        tcs = result.tool_calls
        if tcs:
            return {"type": "tool_call", "tool": tcs[0]["name"], "arguments": tcs[0]["arguments"], "reason": ""}
        content = result.content or ""
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(data, dict):
            return None
        ttype = data.get("type")
        if ttype in ("tool_call", "stop"):
            return data
        return None

    def _execute_tool(self, tool_name, arguments, task_id, card) -> dict:
        if tool_name not in self.tool_whitelist:
            raise ToolSpecError(f"tool '{tool_name}' 未授权")
        coerced = validate_and_coerce(arguments, tool_name, self.max_records)
        tool_fn = get_tool(tool_name)
        res = tool_fn(self.datasource, self.snapshot, self.evidence_store, **coerced)
        self.event_repo.append_event(task_id, "subtask_tool", {
            "card_id": card.card_id, "tool": tool_name, "arguments": coerced,
            "sample_size": res.get("sample_size", 0),
        })
        return {"name": res["name"], "sample_size": res.get("sample_size", 0),
                "result": {"comment_count": res.get("result", {}) if isinstance(res.get("result"), dict) else {}},
                "evidence_ids": res.get("evidence_ids", []), "bias_note": res.get("bias_note", "")}

    def _build_messages(self, card, task_id) -> list[dict]:
        system = (
            f"你是舆情调查子 Agent（Investigator）。你负责深入回答调查卡：[{card.title}]，"
            f"目标：{card.objective}。\n"
            f"本卡建议工具：{', '.join(card.suggested_tools) or '（由你自主选择）'}。\n"
            "你只能使用提供的工具（function calling），每次最多调用一个工具。"
            "工具返回后，结合结果判断是否需要进一步下钻（再次调用工具），"
            "或证据已足/数据不足时停止。\n"
            "停止时，请在 content 中用 JSON 输出："
            "{\"type\":\"stop\",\"stop_reason\":\"evidence_sufficient|data_insufficient|budget_exhausted\","
            "\"summary\":\"...\",\"findings\":[...],\"hypothesis\":\"...\"}。"
        )
        user = (
            f"调查卡：{card.to_dict()}\n"
            "关键证据要求：" + "；".join(card.evidence_requirements or ["（无前置要求）"])
        )
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def _append_tool_result(self, messages, tool_name, summary, reason) -> list[dict]:
        msg = {
            "role": "user",
            "content": json.dumps({
                "type": "tool_result", "tool": tool_name, "tool_result": summary,
                "reason": reason or "",
            }, ensure_ascii=False),
        }
        return messages + [msg]

    def _stop(self, task_id, card, reason, summary, tool_calls_used, findings=None, hypothesis="") -> InvestigatorResult:
        result = InvestigatorResult(
            stop_reason=reason, summary=summary, findings=findings or [],
            tool_calls_used=tool_calls_used, hypothesis=hypothesis,
        )
        self.event_repo.append_event(task_id, "subtask_stop", {
            "card_id": card.card_id, "stop_reason": reason, "summary": summary,
            "tool_calls_used": tool_calls_used,
        })
        return result
