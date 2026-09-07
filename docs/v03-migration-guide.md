# V0.3 迁移指南

## 概述

V0.3 将 V0.2 的固定分析流水线升级为可审计的分阶段工作流，引入 Skill 机制。本文档说明如何从 V0.2 迁移到 V0.3。

## 破坏性变更

**无**。V0.3 完全向下兼容 V0.2。

## 新增功能

1. **Skill 机制**：YAML 声明式配置定义 Skill（stages + 工具白名单 + prompts + output_schema）
2. **工作流引擎**：按 Skill 定义的阶段顺序执行，工具授权在运行时拦截
3. **细粒度审计**：记录每个 stage 开始/完成、每次工具调用、未授权调用被拒绝
4. **证据引用图**：支持 judgment / assumption 证据类型，记录双向引用关系
5. **前端 timeline 视图**：展示 stage 与工具调用过程，停止原因可视化

## 迁移步骤

### 1. 更新依赖

```bash
pip install pyyaml>=6.0 jsonschema>=4.20.0
```

### 2. 配置扩展

在 `.env` 中追加（可选，默认值已足够）：

```ini
SKILLS_DIR=skills
DEFAULT_SKILL=opinion-pulse
WORKFLOW_STAGE_TIMEOUT_MS=300000
ENABLE_WORKFLOW_ENGINE=true
```

### 3. 数据库 schema 扩展

V0.3 在 `tasks` 表新增 `skill_name` 字段。无需手动迁移，`TaskRepository.init_schema()` 会自动创建。

已有 V0.2 任务的 `skill_name` 为 `NULL`，前端展示为"基线模式 (V0.2)"。

### 4. 验证 Skill 加载

```bash
pytest tests/test_skill_loader.py -v
```

### 5. 启用 V0.3 工作流

默认已启用（`ENABLE_WORKFLOW_ENGINE=true`）。创建新任务时，系统自动选择 Skill 并启动工作流引擎。

### 6. 前端验证

访问任务详情页，V0.3 任务会展示 Timeline 视图，V0.2 任务不展示。

## 回退到 V0.2

如需回退，设置 `ENABLE_WORKFLOW_ENGINE=false` 并重启应用。所有新任务自动使用 V0.2 基线流水线。

## 常见问题

**Q: V0.2 已有任务会被影响吗？**

A: 不会。已有任务的 `skill_name` 为 `NULL`，前端可正常查看历史报告与证据。

**Q: 如何自定义 Skill？**

A: 在 `skills/` 目录下创建 YAML 文件，定义 stages、tools、prompts、output_schema。参考 `skills/opinion-pulse.yaml`。

**Q: 工具授权失败如何排查？**

A: 查看任务的 timeline 视图，失败的 stage 会标红并显示"未授权工具: xxx"。检查 Skill YAML 中该 stage 的 `tools` 列表是否包含该工具。

**Q: 如何验证 V0.3 正常工作？**

A: 运行集成测试：`pytest tests/test_workflow_integration.py -v`

真实 LLM 冒烟测试：`pytest tests/test_v03_smoke.py -v -m manual`（需配置真实 LLM API）
