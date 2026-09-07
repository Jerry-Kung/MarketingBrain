"""工作流引擎（按 Skill 定义的 stages 顺序执行，运行时拦截工具调用）。"""
import json

from jsonschema import validate, ValidationError as JsonSchemaValidationError

from app.analysis.registry import get_tool, ToolNotFoundError
from app.workflow.context import build_stage_context


class ToolAuthorizationError(Exception):
    """工具授权失败（LLM 尝试调用未授权工具）。"""


class WorkflowExecutionError(Exception):
    """工作流执行失败（Stage 执行失败、输出格式非法等）。"""


class WorkflowEngine:
    """工作流引擎：按 Skill 定义的阶段顺序执行，工具授权在运行时拦截。"""

    def __init__(
        self,
        skill,
        datasource,
        snapshot,
        evidence_store,
        llm_provider,
        task_repo,
        event_repo,
        *,
        stage_timeout: float | None = None,
    ):
        self.skill = skill
        self.datasource = datasource
        self.snapshot = snapshot
        self.evidence_store = evidence_store
        self.llm_provider = llm_provider
        # task_repo 注入但当前未使用：V0.3 暂不持久化 stage 级状态（仅最终结果落库）。
        # 保留注入，为后续需要按 stage 持久化进度/中间结果，或跨阶段复用任务记录留口。
        # 若确认为无用，删除时需要同步修改 app/workflow/runner.py 的调用处。
        self.task_repo = task_repo
        self.event_repo = event_repo
        # 单阶段 LLM 调用超时（秒）。来自 settings.workflow_stage_timeout（WORKFLOW_STAGE_TIMEOUT_MS）。
        # 透传给 llm_provider.chat(timeout=...)；provider 不强制，由调用方提供。
        self.stage_timeout = stage_timeout
        # 各 stage 的已验证输出（stage.name -> parsed dict），供 _build_result 汇总。
        self._stage_outputs: dict[str, dict] = {}

    def run(self, task_id: str) -> dict:
        """执行完整 Skill 定义的 stages，返回最终 result。

        Args:
            task_id: 任务 ID

        Returns:
            result dict（包含最终报告）

        Raises:
            ToolAuthorizationError: 工具授权失败
            WorkflowExecutionError: Stage 执行失败
        """
        self.event_repo.append_event(
            task_id, "skill_selected", {"skill_name": self.skill.name}
        )

        for stage in self.skill.stages:
            self._execute_stage(task_id, stage)

        return self._build_result()

    def _execute_stage(self, task_id: str, stage):
        """执行单个 Stage：调用 LLM、拦截工具调用、校验输出 schema。"""
        self.event_repo.append_event(
            task_id, "stage_start", {"stage": stage.name}
        )

        try:
            # 调用真实 LLM Provider（C3：原名 _mock_llm_response 有误导性，实际非 mock）。
            # 注意：LLMProvider.chat() 目前不接受 tools 参数（工具选择左待 V0.4），
            # 对真实模型只传 messages；确定性工具由引擎按 stage.tools 全量执行。
            response = self._call_llm_for_stage(stage)

            # 检查工具调用授权
            if "tool_calls" in response:
                for call in response["tool_calls"]:
                    tool_name = call["name"]
                    if tool_name not in stage.tools:
                        error_msg = (
                            f"Stage '{stage.name}' attempted unauthorized tool call: '{tool_name}'. "
                            f"Allowed tools: {', '.join(stage.tools)}"
                        )
                        self.event_repo.append_event(
                            task_id,
                            "tool_unauthorized",
                            {
                                "stage": stage.name,
                                "tool": tool_name,
                                "allowed": stage.tools,
                            },
                        )
                        raise ToolAuthorizationError(error_msg)

                    # 执行授权工具
                    tool_fn = get_tool(tool_name)
                    tool_result = tool_fn(
                        self.datasource, self.snapshot, self.evidence_store
                    )

                    self.event_repo.append_event(
                        task_id,
                        "tool_call",
                        {
                            "stage": stage.name,
                            "tool": tool_name,
                            "params": call.get("arguments", {}),
                            "sample_size": tool_result.get("sample_size", 0),
                        },
                    )

            # 校验 Stage 输出 schema
            self._validate_stage_output(stage, response.get("content", "{}"))

            # C2：累积该 stage 的已验证输出（parsed dict），供 _build_result 汇总。
            output_data = json.loads(response.get("content", "{}"))
            if isinstance(output_data, dict):
                self._stage_outputs[stage.name] = output_data
                # I2：synthesize 类 stage 输出含 judgments/findings 时登记结构化判断；
                # 含 assumptions 时登记假设（机制预留，V0.3 启用）。
                self._extract_and_register_judgments(task_id, stage, output_data)
                self._extract_and_register_assumptions(task_id, stage, output_data)

            self.event_repo.append_event(
                task_id, "stage_done", {"stage": stage.name}
            )

        except (ToolNotFoundError, WorkflowExecutionError) as e:
            self.event_repo.append_event(
                task_id,
                "stage_failed",
                {
                    "stage": stage.name,
                    "error": str(e),
                    "error_type": type(e).__name__,
                },
            )
            if isinstance(e, WorkflowExecutionError):
                raise
            raise WorkflowExecutionError(
                f"Stage '{stage.name}' execution failed: {e}"
            ) from e

    def _validate_stage_output(self, stage, content: str):
        """校验 Stage 输出是否符合 output_schema。"""
        try:
            output_data = json.loads(content)
        except json.JSONDecodeError as e:
            raise WorkflowExecutionError(
                f"Stage '{stage.name}' output is not valid JSON: {e}"
            ) from e

        if stage.output_schema:
            try:
                validate(instance=output_data, schema=stage.output_schema)
            except JsonSchemaValidationError as e:
                raise WorkflowExecutionError(
                    f"Stage '{stage.name}' output does not match schema: {e.message}"
                ) from e

    def _call_llm_for_stage(self, stage) -> dict:
        """调用 LLM Provider 获取 Stage 响应（C3：重命名自 _mock_llm_response）。

        Args:
            stage: 当前 Stage 定义

        Returns:
            dict: {"content": str, "tool_calls": [{"name":..., "arguments":...}]}
        """
        messages = build_stage_context(stage, self.evidence_store)
        # I1：将 settings.workflow_stage_timeout 透传给 provider 作为单次调用超时。
        # 超时边界说明：此处仅在调用处透传超时；若 provider 未实现真实超时（httpx 默认
        # 由 client timeout 兜底），则不在此做额外（如 asyncio.wait_for）包装，超时由
        # provider 自身 / 外层应用负责。
        if self.stage_timeout is not None:
            result = self.llm_provider.chat(messages, timeout=self.stage_timeout)
        else:
            result = self.llm_provider.chat(messages)
        # 为 stage 声明的全部工具生成调用（确定性执行，无需 LLM 主动选择）。
        # 注意：LLMProvider.chat() 不接受 tools 参数，真实 LLM 工具选择左待 V0.4。
        tool_calls = [{"name": t, "arguments": {}} for t in stage.tools]
        return {
            "content": result.content,
            "tool_calls": tool_calls,
        }

    def _build_result(self) -> dict:
        """构建最终 result（包含报告与证据）。

        C2：报告从最后一个 stage 的已验证输出中提取——若该输出含 "report" 键则用之；
        否则将该输出内容整体作为报告文本。无输出或非 dict 时回退到占位报告。
        """
        report = None
        if self._stage_outputs:
            last_output = next(reversed(self._stage_outputs.values()))
            if isinstance(last_output, dict):
                report = last_output.get("report", last_output)
        if report is None:
            report = {"title": "V0.3 工作流报告", "summary": "测试"}
        return {
            "report": report,
            "evidence": [e.to_dict() for e in self.evidence_store.all_records()],
        }

    def _extract_and_register_judgments(self, task_id, stage, output_data: dict) -> None:
        """从 stage 输出中提取 judgments/findings 并登记结构化判断（I2）。

        若输出含 `judgments` 或 `findings` 列表，则逐条调用 evidence_store.register_judgment，
        并发射 `judgment_made` 事件。机制上判断可引用证据；V0.3 简化：当判断未显式给出
        证据引用时，默认引用该时刻已登记的全部证据（保证引用图非空、可校验）。
        """
        raw = output_data.get("judgments")
        if raw is None:
            raw = output_data.get("findings")
        if not isinstance(raw, list):
            return

        # 当前已登记的证据 ID（供未显式引用证据的判断使用）。
        existing_ids = [r.evidence_id for r in self.evidence_store.all_records()]

        for entry in raw:
            if isinstance(entry, str):
                # findings 为纯字符串列表：登记为一条 theme 判断
                judgment = {
                    "judgment_type": "theme",
                    "title": entry,
                    "evidence_refs": existing_ids,
                }
            elif isinstance(entry, dict):
                judgment = dict(entry)
                judgment.setdefault("judgment_type", "theme")
                judgment.setdefault("title", judgment.get("title") or "judgment")
                judgment.setdefault("evidence_refs", existing_ids)
            else:
                continue

            refs = judgment.get("evidence_refs") or []
            ev = self.evidence_store.register_judgment(
                judgment_type=judgment["judgment_type"],
                title=judgment["title"],
                evidence_refs=refs,
                source=stage.name,
            )
            self.event_repo.append_event(
                task_id,
                "judgment_made",
                {
                    "stage": stage.name,
                    "judgment_id": ev.evidence_id,
                    "judgment_type": judgment["judgment_type"],
                    "title": judgment["title"],
                    "evidence_refs": refs,
                },
            )

    def _extract_and_register_assumptions(self, task_id, stage, output_data: dict) -> None:
        """从 stage 输出中提取 assumptions 并登记假设（I2，机制预留）。

        若输出含 `assumptions` 列表，则逐条调用 evidence_store.register_assumption，
        并发射 `assumption_added` 事件。
        """
        raw = output_data.get("assumptions")
        if not isinstance(raw, list):
            return

        for entry in raw:
            if isinstance(entry, str):
                assumption = {"title": entry, "rationale": ""}
            elif isinstance(entry, dict):
                assumption = dict(entry)
                assumption.setdefault("title", assumption.get("title") or "assumption")
                assumption.setdefault("rationale", "")
            else:
                continue

            ev = self.evidence_store.register_assumption(
                title=assumption["title"],
                rationale=assumption["rationale"],
                source=stage.name,
            )
            self.event_repo.append_event(
                task_id,
                "assumption_added",
                {
                    "stage": stage.name,
                    "assumption_id": ev.evidence_id,
                    "title": assumption["title"],
                },
            )
