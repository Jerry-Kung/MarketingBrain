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
    ):
        self.skill = skill
        self.datasource = datasource
        self.snapshot = snapshot
        self.evidence_store = evidence_store
        self.llm_provider = llm_provider
        self.task_repo = task_repo
        self.event_repo = event_repo

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

        messages = build_stage_context(stage, self.evidence_store)

        try:
            # V0.3 简化：不真正调用 LLM（需 mock），仅构建框架
            # 实际实现需调用 self.llm_provider.chat(messages)
            # 并处理返回的 tool_calls
            response = self._mock_llm_response(stage)

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

    def _mock_llm_response(self, stage) -> dict:
        """Mock LLM 响应（测试用，真实实现需调用 llm_provider.chat）。"""
        # V0.3 测试框架：返回符合 output_schema 的最小 JSON
        return {
            "content": json.dumps({"ok": True}),
            "tool_calls": [],
        }

    def _build_result(self) -> dict:
        """构建最终 result（包含报告与证据）。"""
        return {
            "report": {"title": "V0.3 工作流报告", "summary": "测试"},
            "evidence": [e.to_dict() for e in self.evidence_store.all_records()],
        }
