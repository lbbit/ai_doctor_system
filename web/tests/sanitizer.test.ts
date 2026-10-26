import { describe, expect, it } from 'vitest';
import { formatSanitizerSummary } from '../src/api/chat';

describe('脱敏摘要展示（A-4/AC-14.2）', () => {
  it('空/无摘要返回 null', () => {
    expect(formatSanitizerSummary(undefined)).toBeNull();
    expect(formatSanitizerSummary([])).toBeNull();
  });

  it('按类型计数并中文化', () => {
    const text = formatSanitizerSummary([
      { type: 'name', repl: '档案对象' },
      { type: 'name', repl: '父亲' },
      { type: 'phone', repl: '***电话***' },
      { type: 'birthday', repl: '约70-79岁' },
    ]);
    expect(text).toBe('已脱敏：姓名×2、电话×1、生日×1');
  });

  it('未知类型透传', () => {
    const text = formatSanitizerSummary([{ type: 'weird', repl: 'x' }]);
    expect(text).toBe('已脱敏：weird×1');
  });
});
