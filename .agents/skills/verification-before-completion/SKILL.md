---
name: verification-before-completion
description: 在宣称完成、修复、通过、可发布或提交前，执行当前树上的新鲜验证并核对证据。
---

# 完成前验证

来源：obra/superpowers `verification-before-completion`，来源链接：https://github.com/obra/superpowers/tree/main/skills/verification-before-completion

## 铁律

没有当前验证证据，不得做完成性结论。

## 检查顺序

1. 明确要证明的主张及唯一/最小证明命令。
2. 在当前工作树运行完整命令，不能引用旧输出或“应该通过”。
3. 读取完整退出码、失败数量和关键输出。
4. 逐条对照需求/计划验收清单，测试通过不等于需求全部满足。
5. 若未通过，报告真实状态并回到 `systematic-debugging`，不得隐藏失败。

## 本项目门槛

后端改动至少跑 pytest；前端改动至少跑 vitest 与 build；跨服务/部署改动跑 Compose healthcheck 和 `scripts/smoke_e2e.sh`；评测改动跑 `score_kpi.py selftest`。真实 KPI 未测量时只能报告“评测框架就绪”。
