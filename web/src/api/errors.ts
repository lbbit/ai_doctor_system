/** 后端错误码 → 中文提示（ARCHITECTURE §4.4；新增码需同步）。 */

export const ERROR_MESSAGES: Record<string, string> = {
  BAD_REQUEST: '请求参数有误',
  UNAUTHENTICATED: '登录已失效，请重新登录',
  TOKEN_EXPIRED: '登录已过期，请重新登录',
  INVALID_CREDENTIALS: '邮箱或密码错误',
  LINK_EXPIRED: '链接已过期，请刷新后重试',
  NOT_FOUND: '内容不存在或无权访问',
  DUP_FILE: '已存在相同文件',
  REVIEW_CONFLICT: '该报告已在其他页面处理，请刷新后重试',
  STATE_INVALID: '当前状态不允许该操作',
  FILE_TOO_LARGE: '文件超过大小限制（最大 20MB）',
  UNSUPPORTED_MEDIA_TYPE: '仅支持 JPG/PNG/PDF 格式',
  SUBJECT_REQUIRED: '请先选择档案对象',
  VALIDATION_ERROR: '表单填写有误，请检查',
  RATE_LIMITED: '操作过于频繁，请稍后再试',
  LLM_UNAVAILABLE: 'AI 服务暂不可用，请稍后重试或手动录入',
  LLM_TIMEOUT: 'AI 服务响应超时，请重试',
  INTERNAL: '服务器内部错误（请保留页面下方 requestId 反馈）',
  NETWORK_ERROR: '网络错误，请检查网络连接',
  CONFLICT: '操作冲突，请刷新后重试',
  FORBIDDEN: '无权访问',
};

export function messageForCode(code: string | undefined, fallback?: string): string {
  if (code && ERROR_MESSAGES[code]) return ERROR_MESSAGES[code];
  return fallback || code || '请求失败';
}
