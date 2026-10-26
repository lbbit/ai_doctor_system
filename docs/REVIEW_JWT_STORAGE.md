# JWT 存储方式安全评审（T0.9）

> 状态：评审结论已出 | 日期：2026-10-20
> 关联：`docs/PRD.md` §7.1（遗留项"按安全评审定"）、F-01；实现见 `web/src/api/client.ts`、`server/app/auth/{router,service,security}.py`

## 1. 评审结论

**P0 维持现状：JWT 存 localStorage + Authorization Bearer 头，判定为可接受风险，不阻塞验收。**
理由：本产品是个人健康档案场景（单用户自有设备为主），当前威胁模型下 XSS 窃取与 Cookie 方案的 CSRF 复杂度收益不对等；且 SSE 链路已依赖 fetch+header（`web/src/api/chat.ts`），换 HttpOnly Cookie 需连带改造。若后续引入共享设备/公共网络登录场景，再按 §9 路径升级。

## 2. 评审范围与依据

| 事实点 | 现状 | 出处 |
|---|---|---|
| Token 前端存储 | `localStorage['aidoctor_token']` | `web/src/api/client.ts` `tokenStore` |
| 请求携带方式 | axios 拦截器统一加 `Authorization: Bearer`；SSE 用 fetch 手动加同名头 | `client.ts` L74-76、`chat.ts` L72-78 |
| JWT 算法/TTL | HS256（settings.jwt_alg），默认 7 天（§8.5） | `server/app/auth/security.py` |
| 刷新机制 | `POST /auth/refresh` 重签发返回新 token（响应体下发，非 Set-Cookie） | `auth/router.py` L28-34 |
| 失效处理 | 401 → 清 localStorage + 全局事件跳登录页 | `main.tsx`、`authStore.ts` |
| CSRF 防护 | 无（Bearer 头方案不需要；浏览器不会自动附带该头） | — |

## 3. 方案优点（现状）

- **天然免疫 CSRF**：凭据不经 Cookie 自动携带，跨站表单/fetch 无法冒用（前提：不引入 withCredentials）。
- **实现简单一致**：SPA、SSE、未来小程序壳（T1.5）共用同一 header 鉴权路径，无 SameSite/CSRF-token 双轨。
- **多端友好**：token 在 JS 层可控，客户端形态不受浏览器 Cookie 语义限制。

## 4. 主要问题

| # | 级别 | 现象 | 影响 | 原因 | 建议动作 |
|---|---|---|---|---|---|
| P1 | 中 | XSS 可经 `localStorage` 直接读走长效 token（7d） | 会话期内账户级数据全泄露+可写 | localStorage 对任何注入脚本可读，HttpOnly Cookie 正是防这个 | 见 §6-M1/M2 |
| P2 | 低 | token 明文落盘（磁盘/备份可见） | 共享设备上物理接触可提取 | localStorage 无加密 | 个人自用设备可接受；文档注明即可 |
| P3 | 低 | TTL 7 天偏长且无服务端吊销 | 被盗后窗口期长；改密/登出无法即时踢线 | 纯无状态 JWT + refresh 仅重签发 | 缩短 access TTL（如 24h），refresh 流程已有，前端拦截器已处理 401→重登 |

## 5. 风险分级汇总

- 阻塞项：无。
- 高风险：无。
- 中风险：P1（XSS → token 窃取）。当前 CSP/Cookie 之外的输入面均有脱敏与参数化查询兜底，但健康档案含敏感 PHI，建议开发阶段内落实 §6。
- 低风险：P2、P3。

## 6. 必须修改项 / 建议优化项

**必须（M0 内）**：无强制改造；本文档即 PRD §7.1 遗留项的评审结论落地。

**建议（M1 初随 deprecation 技术债一并做）**：
- M1-a：access TTL 降至 ≤24h，配合既有 `/auth/refresh` 静默续期（前端 `client.ts` 响应拦截器加一次 refresh-retry）。
- M1-b：上 CSP（至少 default-src 'self'），把 XSS 概率压到可接受——localStorage 方案的安全下限取决于 CSP 质量。
- M1-c：登录失败限速 + 密码策略校验前移（现状 bcrypt 已有，防爆破未做）。

**升级路径（若威胁模型变化才启动）**：HttpOnly+Secure+SameSite=Lax Cookie 承载会话 cookie（短 TTL）+ CSRF token 双提交；SSE/fetch 改 credentials。代价：后端需 Set-Cookie 分支、CSRF 中间件、小程序端仍需 header 通道 → 双轨维护。不选原因见 §8。

## 7. 可接受风险

- P2 明文落盘（个人设备前提成立时）。
- localStorage 与 subjectStore 共用存储区，被注入脚本连坐读取——依赖 CSP 兜底。

## 8. trade-off：为什么不现在换 HttpOnly Cookie

| 维度 | localStorage+Bearer（现） | HttpOnly Cookie |
|---|---|---|
| XSS 窃取 | ✗ 可读走 | ✓ JS 不可读 |
| CSRF | ✓ 天然免疫 | ✗ 需 SameSite+token 双防护 |
| SSE/多端一致 | ✓ 统一 header | ✗ EventSource 不能带自定义头，Cookie 域语义受限 |
| 实现复杂度 | 低（已上线） | 中（Set-Cookie/CSRF/CORS credentials） |
| 吊销能力 | 都弱（无状态 JWT），P3 另议 | 同左 |

结论：在"个人自有设备 + SPA/SSE/小程序多形态"前提下，现状综合最优；XSS 防线交给 CSP 而非存储介质。

## 9. 下一步建议

1. M0：本文档作为 PRD §7.1 遗留项关闭依据，PLAN.md T0.9 → ✅。
2. M1 初：落实 M1-a/M1-b/M1-c（半天工作量）。
3. 触发重评条件：出现共享设备登录、第三方嵌入页、或引入 withCredentials 任何一处。
