# 项目级 Agent 技能

这些技能是项目内可审计的工作流规则，按 `obra/superpowers` 公开技能整理，来源和用途写在每个 `SKILL.md` 的 frontmatter/正文中。

推荐顺序：

1. 复杂需求：`brainstorming`
2. 多步骤落地：`writing-plans`
3. 计划执行：`executing-plans`；任务独立且有子代理时可用 `subagent-driven-development`
4. 编码与修复：`test-driven-development`
5. 异常排查：`systematic-debugging`
6. 完成声明前：`verification-before-completion`
7. 交付收尾：`finishing-a-development-branch`

网络恢复后可用官方 CLI 同步上游版本：

```powershell
npx skills add https://github.com/obra/superpowers --skill executing-plans --yes
```

同步时保留项目特有的医疗数据安全、无 Git 证据留存和验证基线要求。
