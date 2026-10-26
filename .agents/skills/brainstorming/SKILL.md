---
name: brainstorming
description: 在创造性需求、新功能、架构调整或行为变更前澄清目标、边界、成功标准和反目标。
---

# 需求与设计澄清

来源：obra/superpowers `brainstorming`，来源链接：https://github.com/obra/superpowers/tree/main/skills/brainstorming

## 使用方式

- 简单、明确、低风险请求直接执行；复杂请求先确认真实使用场景和成功标准。
- 先复述已知目标，再标出假设、非目标、约束和会导致失败的情况。
- 对已有项目优先阅读现状和相邻实现；方案必须顺应已有模块边界，而不是凭空重建。
- 大需求拆成可独立验收的子项目；写成设计文档后再转实施计划。
- 医疗场景特别确认：谁是 Subject、谁能写入、依据如何回链、无依据如何表达、风险提示由谁决定。

## 输出

设计文档使用“背景与目标、范围、现状、约束、方案、模块/接口、取舍、实施、风险、验收、待确认项”结构，保存到 `docs/superpowers/specs/`；获批后只进入 `writing-plans`。
