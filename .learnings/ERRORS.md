# 工具与环境错误记录

## [ERR-20261021-001] execution-subagent-model

**Logged**: 2026-10-21
**Priority**: medium
**Status**: pending
**Area**: tooling

### Summary
执行子代理请求因当前环境不支持请求模型而返回 `model_not_supported`，未执行项目命令。

### Details
本次请求只包含后端 pytest、前端 vitest 和 build，工具返回 HTTP 400。该错误属于 Agent 执行环境能力限制，不代表项目验证失败；已改用本地终端直接执行。

### Suggested Action
后续需要执行子代理时先确认当前会话支持的模型；不支持时使用本地终端或可用的 Explore/架构代理，不重复盲试同一请求。

### Metadata
- Source: external_tool_error
- Related Files: AGENTS.md
- Tags: execution-subagent, model-support, verification

## [ERR-20261021-002] inline-python-multipart-test

**Logged**: 2026-10-21
**Priority**: low
**Status**: resolved
**Area**: tests

### Summary
PowerShell 传入 `python -c` 的多语句脚本包含 `with` 代码块，触发 Python 语法错误。

### Details
错误发生在本地检测单上传测试脚本组装阶段，请求尚未发送到 API。改用临时脚本文件后可保留清晰的代码块结构，执行后删除临时文件。

### Suggested Action
Windows 上执行包含多行 Python 控制流的临时验证，优先使用短期脚本文件，不要把 `with`/循环压缩到 `python -c` 单行。

### Metadata
- Source: command_error
- Related Files: docs/testinfo/llmtestapi.md
- Tags: powershell, python, multipart

## [ERR-20261021-003] local-test-requests-dependency

**Logged**: 2026-10-21
**Priority**: low
**Status**: resolved
**Area**: tests

### Summary
一次性检测单验证脚本依赖 `requests`，但项目虚拟环境未安装该库。

### Details
脚本在导入阶段失败，未发出 API 请求。项目已有 curl 和 PowerShell JSON 能力，因此无需新增测试依赖。

### Suggested Action
临时 HTTP 验证优先使用已有命令行工具；长期需要脚本化 HTTP 客户端时再评估正式依赖和 requirements 变更。

### Metadata
- Source: command_error
- Related Files: server/requirements.txt
- Tags: python-dependencies, curl, test-harness
