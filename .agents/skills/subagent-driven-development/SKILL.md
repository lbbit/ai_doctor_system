---
name: subagent-driven-development
description: 对有实施计划且任务相对独立的工作，按任务派发实现者、规格审查者和质量审查者，并保留可恢复账本。
---

# 子代理驱动开发

来源：obra/superpowers `subagent-driven-development`，来源链接：https://github.com/obra/superpowers/tree/main/skills/subagent-driven-development

## 使用条件

任务已有明确计划、任务边界相对独立，并且环境提供可用子代理时使用；任务高度耦合或没有计划时用 `executing-plans`。

## 每任务循环

1. 记录基线和任务 brief，向实现者只提供本任务需求、接口和全局约束。
2. 实现者按 TDD 完成并报告测试证据，不再自行派发重复审查者。
3. 生成任务差异包，分别检查规格符合性和代码质量；Critical/Important 必须修复。
4. 修复后做有范围的复审；最多五轮，连续失败说明需要重新审视计划/架构。
5. 在账本中记录任务状态、基线、变更、测试、审查结论和延期 minor。
6. 全部任务后做一次新鲜的全局审查，再执行分支收尾流程。

## 本项目注意

涉及 Subject 隔离、审计、脱敏、数据库迁移、状态机的任务不能并行修改同一契约；先完成契约任务，再交给依赖任务。没有 Git 时不能假装存在 commit range，改用文件快照和命令日志。
