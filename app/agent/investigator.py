"""Investigator 子 Agent：单张调查卡的受控调查 Loop。

通过真实 function calling 从白名单工具中选择调用，依据工具结果（证据摘要）决定
继续下钻或停止。每轮开始前检查预算，超限即停止。工具名与参数必须通过授权与校验。
"""
import json

from app.agent.protocols import (
    InvestigationCard, InvestigatorResult,
    STOP_REASON_DATA_INSUFFICIENT, STOP_REASON_BUDGET, STOP_REASON_TOOL_FAILURE,
    STOP_REASON_ILLEGAL, VALID_STOP_REASONS,
)
from app.agent.tools_spec import (
    TOOL_JSON_SCHEMAS, TOOL_WHITELIST, validate_and_coerce, ToolSpecError,
)
from app.analysis.registry import get_tool
from app.llm.provider import LLMResult


def _extract_turn_json(content: str | None):
    """从 LLM content 中容错提取 stop/tool_call 的 JSON 对象。

    真实 LLM 在停止时并非总输出纯 JSON，常见形态：
    - 纯 JSON：{"type":"stop",...}
    - ```json ... ``` 代码围栏包裹
    - 散文 + 首尾 { } 包裹的 JSON（如『调查结论如下：{...}』）
    此函数按优先级尝试：纯 JSON 直解 -> 去掉 ```json 围栏后直解 -> 抽取首尾 { } 片段。
    解析失败或非 dict 返回 None。
    """
    if content is None:
        return None
    s = content.strip()
    if not s:
        return None
    try:
        data = json.loads(s)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, TypeError):
        pass
    # 去掉 ```json ... ``` 代码围栏
    if s.startswith("```"):
        nl = s.find("\n")
        fence = s[:nl] if nl != -1 else s
        if fence.strip().strip("`").strip().lower() in ("", "json"):
            body = s[nl + 1:] if nl != -1 else ""
            if body.rstrip().endswith("```"):
                body = body.rstrip()[:-3]
            s = body.strip()
    try:
        data = json.loads(s)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, TypeError):
        pass
    # 抽取首尾 { } 之间的 JSON 片段（散文包裹时）
    i = s.find("{")
    j = s.rfind("}")
    if i != -1 and j != -1 and j >= i:
        frag = s[i:j + 1]
        try:
            data = json.loads(frag)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, TypeError):
            pass
    return None


def _stringify_findings(findings) -> list[str]:
    """把 findings 规范为字符串列表。

    LLM 常把 findings 输出为对象数组（如 [{"node":...,"event":...}]）而非字符串数组，
    InvestigatorResult.findings 约定为 list[str]，非字符串项在 _stop 时会被
    Orchestrator 当作字典处理导致后续组装异常。此处统一转为精简摘要字符串。
    """
    if not isinstance(findings, list):
        return []
    out = []
    for f in findings:
        if isinstance(f, str):
            out.append(f)
        elif isinstance(f, dict):
            # 优先取可读字段，缺省回退首项与尾项
            title = (f.get("title") or f.get("node") or f.get("event")
                     or (list(f.values())[0] if f else ""))
            detail = f.get("event") or f.get("detail") or ""
            out.append(f"{title}：{detail}".strip("：") if detail else str(title))
        else:
            out.append(str(f))
    return out


def _result_summary(res: dict) -> dict:
    """把工具返回结果压缩为紧凑可读摘要，避免审计事件膨胀。

    各工具的 result dict 形如：
    - data_coverage: {"comment_count", "datasource"}
    - volume_trend: {"total"}
    - period_comparison: {"current", "previous", "change_rate"}
    - topic_frequency_tool: {"topics": [{"topic", "comment_count"}]}
    - top_sources: {"videos": [{"job_id", "video_title", "comment_count", ...}]}
    - sample_comments / drill_evidence: {"comments": [...]}
    - object_compare: {"current", "other", "change_rate"}

    逐字段抽取，list 最多保留前 3 项（每项仅保留 title/job_id/count 等标量），
    缺失/非标量安全回退为 sample_size 计数，保证事件体量可控。
    """
    result = res.get("result")
    if not isinstance(result, dict):
        return {"sample_size": res.get("sample_size", 0)}
    summary = {}

    def _take(key):
        v = result.get(key)
        if v is not None:
            summary[key] = v

    for key in ("comment_count", "datasource", "total", "current", "previous",
                "change_rate", "sample_size"):
        _take(key)

    if isinstance(result.get("topics"), list):
        summary["topics"] = [
            {"topic": t.get("topic"), "comment_count": t.get("comment_count")}
            for t in result["topics"][:3] if isinstance(t, dict)
        ]
        summary["topic_count"] = len(result["topics"])

    if isinstance(result.get("videos"), list):
        summary["videos"] = [
            {"job_id": v.get("job_id"), "video_title": v.get("video_title"),
             "comment_count": v.get("comment_count")}
            for v in result["videos"][:3] if isinstance(v, dict)
        ]
        summary["video_count"] = len(result["videos"])

    if isinstance(result.get("comments"), list):
        summary["comment_count"] = len(result["comments"])

    if "comment_count" not in summary:
        summary["sample_size"] = res.get("sample_size", 0)
    return summary


def _raw_message(raw: dict) -> dict:
    """从 raw 中取出 assistant message（缺失时返回空 dict）。"""
    try:
        return dict(raw["choices"][0]["message"])
    except (KeyError, IndexError, TypeError):
        return {}


def _assistant_message(raw: dict) -> dict:
    """构造可回传的 assistant 消息，保留 tool_calls 与 reasoning_content。"""
    msg = _raw_message(raw)
    asst: dict = {"role": "assistant"}
    # 保留 reasoning_content（Qwen 思考型模型），避免上下文断裂
    if msg.get("reasoning_content"):
        asst["reasoning_content"] = msg["reasoning_content"]
    if msg.get("content"):
        asst["content"] = msg["content"]
    if msg.get("tool_calls"):
        asst["tool_calls"] = msg["tool_calls"]
    return asst


def _tool_call_id(raw: dict, tool_name: str, index: int = 0) -> str:
    """取本轮第 index 个 tool_call 的 id（用于 role='tool' 的 tool_call_id）。"""
    msg = _raw_message(raw)
    calls = msg.get("tool_calls") or []
    if index < len(calls):
        cid = calls[index].get("id")
        if cid:
            return str(cid)
    # 缺省装配一个稳定 id，避免工具配对失败
    return f"call-{tool_name}"


class Investigator:
    """子 Agent：围绕一张调查卡开展有限探索。"""

    def __init__(self, llm_provider, datasource, snapshot, evidence_store,
                 event_repo, budget_counter, *, tool_whitelist=None,
                 max_loops=None, max_records=None, settings=None):
        self.llm_provider = llm_provider
        self.datasource = datasource
        self.snapshot = snapshot
        self.evidence_store = evidence_store
        self.event_repo = event_repo
        self.budget_counter = budget_counter
        self.tool_whitelist = tool_whitelist or set(TOOL_WHITELIST)
        self.max_loops = max_loops
        self.max_records = max_records or 500
        self.settings = settings

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
            messages = self._append_tool_result(
                messages, tool_name, tool_summary, turn.get("reason", ""), result)

    def _call_llm(self, messages) -> LLMResult:
        # settings 为 None（测试直接实例化）时缺省不设超时；生产路径由 Orchestrator 传入。
        timeout = getattr(self.settings, "agent_llm_timeout", None)
        # max_tokens 需要考虑 reasoning_content（思考链）占用：若只给 2000，
        # 长摘要+思考会截断，导致 stop JSON 不完整。这里放宽到 4000。
        return self.llm_provider.chat(
            messages, tools=TOOL_JSON_SCHEMAS, temperature=0.2, max_tokens=4000,
            timeout=timeout,
        )

    def _parse_turn(self, result: LLMResult) -> dict | None:
        """从 LLMResult 解析合法回合 dict，或 None。

        优先级：tool_calls（function calling）> content 中的 {"type":"stop"/"tool_call"}。
        content 解析做容错：纯 JSON / ```json 围栏 / 散文包裹三种形态均可提取。
        """
        tcs = result.tool_calls
        if tcs:
            return {"type": "tool_call", "tool": tcs[0]["name"], "arguments": tcs[0]["arguments"], "reason": ""}
        content = result.content or ""
        data = _extract_turn_json(content)
        if not isinstance(data, dict):
            return None
        ttype = data.get("type")
        if ttype in ("tool_call", "stop"):
            # findings 统一规范为字符串列表，避免对象数组导致后续组装异常
            if "findings" in data:
                data["findings"] = _stringify_findings(data.get("findings"))
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
            "result_summary": _result_summary(res),
            "bias_note": res.get("bias_note", ""),
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

    def _append_tool_result(self, messages, tool_name, summary, reason, result) -> list[dict]:
        """追加本轮 assistant 消息 + tool 结果，维持 function-calling 会话协议。

        OpenAI 协议要求：assistant 先输出 tool_calls，随后以 role="tool" 附带
        tool_call_id 返回结果。此前只追加 role="user" 的工具结果、且不回传
        assistant 的 tool_calls，模型会因缺少 tool_call 配对而逐步偏离函数调用
        轨道，退化为纯文本/散文输出（这是"LLM 输出非法"的重要诱因）。
        同时保留 Qwen 等模型的 reasoning_content，让上下文完整。
        """
        asst = _assistant_message(result.raw)
        tool_result = {
            "type": "tool_result", "tool": tool_name, "tool_result": summary,
            "reason": reason or "",
        }
        tool_msg = {"role": "tool", "tool_call_id": _tool_call_id(result.raw, tool_name),
                    "content": json.dumps(tool_result, ensure_ascii=False)}
        return messages + [asst] + [tool_msg]

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
