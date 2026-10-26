---
name: writing-plans
description: 为跨文件、多步骤或长期需求编写可执行实施计划，明确文件、接口、测试、预期输出和风险。
---

# 编写实施计划

来源：obra/superpowers `writing-plans`，来源链接：https://github.com/obra/superpowers/tree/main/skills/writing-plans

## 计划要求

- 先写目标、架构、技术栈、依据文档、全局约束和审查重点。
- 先映射文件职责，再拆任务；每个任务必须能独立测试，避免把多个子系统混成一个大任务。
- 每个步骤只做一件可检查的事：写失败测试、运行并确认失败、写最小实现、运行并确认通过、记录变更。
- 指定准确路径、符号名、参数/返回类型、命令和 Expected 结果；不要使用“适当处理”“补充测试”这类不可执行描述。
- 明确不做什么、兼容性、迁移、回滚、观测和验收门槛。

## 本项目模板

计划优先保存到 `docs/superpowers/plans/YYYY-MM-DD-<topic>.md`，长期状态同步到 `PLAN.md`。医疗数据、Subject 隔离、审计、脱敏、状态机、幂等和降级必须出现在 Review Focus 中。

## 自检

逐条核对 PRD/架构文档覆盖、任务接口一致性、每个失败模式是否有测试、计划是否过度规定实现细节。计划完成后先让人审阅，再进入 `executing-plans` 或 `subagent-driven-development`。
