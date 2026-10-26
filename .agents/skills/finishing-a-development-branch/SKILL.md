---
name: finishing-a-development-branch
description: 所有实现和测试完成后，验证当前树并决定如何保留、合并或发布变更。
---

# 开发分支收尾

来源：obra/superpowers `finishing-a-development-branch`，来源链接：https://github.com/obra/superpowers/tree/main/skills/finishing-a-development-branch

## 流程

1. 运行项目全量测试与必要构建；失败就停在失败状态，不进入发布选项。
2. 检查工作树、分支、基线和是否存在未记录文件；没有 Git 时输出目录快照与验证清单。
3. 核对计划退出标准、风险和延期项，更新 `PLAN.md`。
4. 未经用户明确授权，不执行 commit、push、merge、PR、删除或清理用户文件。
5. 保留可恢复的计划、账本、测试输出和变更记录。

## 医疗项目额外门槛

Compose/生产配置变化必须有冒烟证据；数据库迁移必须验证升级路径；安全边界变化必须有跨档、未授权和审计测试；真实 KPI 未完成不可标记 P0 Go。
