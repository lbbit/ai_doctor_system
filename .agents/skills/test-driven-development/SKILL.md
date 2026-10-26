---
name: test-driven-development
description: 实现新功能、修复缺陷、重构或行为变更时，强制执行 RED-GREEN-REFACTOR。
---

# 测试驱动开发

来源：obra/superpowers `test-driven-development`，来源链接：https://github.com/obra/superpowers/tree/main/skills/test-driven-development

## 铁律

没有先失败的测试，就不要写生产代码。

## 循环

1. 写一个只验证一个行为的真实测试，优先覆盖用户可观察结果和安全边界。
2. 运行该测试，确认它因缺少目标行为而失败；若是拼写、fixture 或环境错误，先修测试。
3. 写能通过测试的最小实现，不顺手扩展接口或重构无关代码。
4. 运行局部测试，再运行项目全量测试；读取退出码和失败数量。
5. 仅在 GREEN 后重构，重构不得改变行为；再次运行全量测试。

## 本项目重点

新增 API 必须覆盖未授权、跨 Account/Subject、缺少 Subject、重复请求和错误降级；涉及医疗抽取必须覆盖草稿未确认不入库、双时间轴、幂等和审计。测试不能仅断言 mock 被调用。

## 例外

纯文档、静态配置或生成文件可不严格走 RED-GREEN，但必须做格式/解析/构建验证，并在计划中说明例外。
