import { describe, expect, it } from 'vitest';
import { CITATION_RE, parseCitations } from '../src/components/CitationTag';

describe('citation 解析（F-10/AC-10.1）', () => {
  it('解析 [依据：标签] 标记', () => {
    const text = '血糖偏高，建议复查。[依据：2026-03-12 空腹血糖] 请就医。';
    const cites = parseCitations(text);
    expect(cites).toHaveLength(1);
    expect(cites[0].label).toBe('2026-03-12 空腹血糖');
  });

  it('解析多个标记并保留出现顺序', () => {
    const text = 'A[依据：第一次]B[依据：第二次]C';
    const cites = parseCitations(text);
    expect(cites.map((c) => c.label)).toEqual(['第一次', '第二次']);
  });

  it('容忍全角冒号与编号', () => {
    expect(parseCitations('x[依据1：报告]')[0].label).toBe('报告');
    expect(parseCitations('x[依据 1: 报告]')[0].label).toBe('报告');
  });

  it('无标记文本返回空数组', () => {
    expect(parseCitations('普通回答，没有引用')).toEqual([]);
  });

  it('正则与解析函数行为一致', () => {
    const text = 'a[依据：X]b';
    expect([...text.matchAll(CITATION_RE)]).toHaveLength(1);
  });
});
