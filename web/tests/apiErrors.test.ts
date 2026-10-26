import { describe, expect, it } from 'vitest';
import { ApiError } from '../src/api/client';
import { ERROR_MESSAGES, messageForCode } from '../src/api/errors';

describe('api/errors', () => {
  it('maps known codes to Chinese messages', () => {
    expect(messageForCode('SUBJECT_REQUIRED')).toBe('请先选择档案对象');
    expect(messageForCode('DUP_FILE')).toBe('已存在相同文件');
    expect(messageForCode('REVIEW_CONFLICT')).toContain('其他页面');
    expect(messageForCode('LLM_TIMEOUT')).toContain('超时');
  });

  it('falls back for unknown codes', () => {
    expect(messageForCode('SOME_NEW_CODE')).toBe('SOME_NEW_CODE');
    expect(messageForCode(undefined, '默认提示')).toBe('默认提示');
  });

  it('covers all §4.4 error codes', () => {
    const codes = [
      'BAD_REQUEST', 'UNAUTHENTICATED', 'TOKEN_EXPIRED', 'INVALID_CREDENTIALS',
      'LINK_EXPIRED', 'NOT_FOUND', 'DUP_FILE', 'REVIEW_CONFLICT', 'STATE_INVALID',
      'FILE_TOO_LARGE', 'UNSUPPORTED_MEDIA_TYPE', 'SUBJECT_REQUIRED',
      'VALIDATION_ERROR', 'RATE_LIMITED', 'LLM_UNAVAILABLE', 'LLM_TIMEOUT', 'INTERNAL',
    ];
    for (const code of codes) {
      expect(ERROR_MESSAGES[code], `missing message for ${code}`).toBeTruthy();
    }
  });

  it('ApiError carries envelope fields', () => {
    const err = new ApiError('NOT_FOUND', '不存在', 404, 'req-1');
    expect(err.code).toBe('NOT_FOUND');
    expect(err.httpStatus).toBe(404);
    expect(err.requestId).toBe('req-1');
  });
});
