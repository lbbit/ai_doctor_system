---
name: executing-plans
description: 执行已批准的多步骤实现计划；逐任务读取 brief、按 TDD 验证、记录偏差和进度，适用于本项目长期迭代。
---

# 执行计划

来源：obra/superpowers `executing-plans`，已按项目约束整理，来源链接：https://github.com/obra/superpowers/tree/main/skills/executing-plans

## 使用条件

- 必须已有可执行计划；计划不清晰时先补计划，不猜接口。
- 先读 `PLAN.md` 和任务计划，再执行；不要凭会话记忆跳过任务。
- 本项目无 Git 时，用 `PLAN.md` 的进度表、`docs/superpowers/ledgers/` 的日志和测试输出替代提交历史。

## 流程

1. 预检：读取产品/架构依据，列任务，检查任务间共享接口和冲突。
2. 逐任务：读取任务 brief，标记进行中，执行每一步并比较 Expected 输出。
3. 每一步遵循 TDD：测试先行、观察 RED、最小实现、观察 GREEN、局部验证、全量验证。
4. 结果不符时区分“代码错误”和“计划错误”；代码错误走 `systematic-debugging`，计划错误写入决策日志再调整。
5. 任务完成必须记录：命令、退出码、关键结果、变更文件、剩余风险和下一任务。
6. 全部任务后做一次独立的全局审查；Critical/Important 必须修复，Minor 明确延期。

## 停止条件

遇到破坏性操作、安全敏感操作、外部发布/合并，或计划已无法合理解释现状时停止并请求决策；普通测试失败不得跳过，先排错。

## 完成证据

不得用“应该可以”作为证据。完成结论必须对应当前树上新鲜的测试/构建/冒烟输出，并回写 `PLAN.md`。
