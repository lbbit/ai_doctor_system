import { describe, expect, it } from 'vitest';
import { parseMarkdown, nodesToText } from '../src/components/markdownTokens';

/**
 * 校验「真实 LLM 回答形态」能被正确解析——这些文本来自实际问诊输出，
 * 用于防止渲染器退化。
 */

const REAL_ANSWER = `已记录：**35.8℃**（本次你连发两条相同内容，按此前规则只记 1 条）。

更新后的补录清单：

| 序号 | 体温 |
|---|---|
| 1 | 36.3℃ |
| 2 | 35.9℃ |

说明与提醒：

- 第 8 条比第 7 条**回升 0.1℃**，波动本身正常
- 整体仍在 36.0℃ 以下

**出现以下情况请尽快就医**：畏寒发抖、嗜睡、手脚发凉。

> 依据有限：档案中未找到与『体温』相关的既往记录[依据 1：2021 化验单]。`;

describe('真实回答渲染', () => {
  it('表格被识别', () => {
    const nodes = parseMarkdown(REAL_ANSWER);
    const table = nodes.find((n) => n.type === 'table');
    expect(table).toBeTruthy();
    if (table && table.type === 'table') {
      expect(table.header).toHaveLength(2);
      expect(table.rows).toHaveLength(2);
    }
  });

  it('列表被识别且包含加粗子节点', () => {
    const nodes = parseMarkdown(REAL_ANSWER);
    const list = nodes.find((n) => n.type === 'list');
    expect(list).toBeTruthy();
    if (list && list.type === 'list') {
      expect(list.items.length).toBe(2);
      expect(JSON.stringify(list)).toContain('strong');
    }
  });

  it('引用块被识别', () => {
    const nodes = parseMarkdown(REAL_ANSWER);
    expect(nodes.some((n) => n.type === 'blockquote')).toBe(true);
  });

  it('引用标记文本完整保留', () => {
    const text = nodesToText(parseMarkdown(REAL_ANSWER));
    expect(text).toContain('[依据 1：2021 化验单]');
  });

  it('关键数值不丢失', () => {
    const text = nodesToText(parseMarkdown(REAL_ANSWER));
    expect(text).toContain('35.8');
    expect(text).toContain('36.3');
  });
});

describe('流式增量渲染稳定性', () => {
  it('未闭合的加粗不会崩溃且保留文本', () => {
    const nodes = parseMarkdown('已记录：**35.8');
    expect(nodesToText(nodes)).toContain('35.8');
  });

  it('未闭合的表格不会崩溃', () => {
    const nodes = parseMarkdown('| 序号 | 体温 |\n|---');
    expect(() => nodesToText(nodes)).not.toThrow();
  });

  it('未闭合的代码块不会崩溃', () => {
    const nodes = parseMarkdown('```\nconst a =');
    expect(() => nodesToText(nodes)).not.toThrow();
  });
});
