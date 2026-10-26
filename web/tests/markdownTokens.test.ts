import { describe, expect, it } from 'vitest';
import { isSafeHref, nodesToText, parseMarkdown } from '../src/components/markdownTokens';

describe('isSafeHref', () => {
  it('允许 http/https', () => {
    expect(isSafeHref('https://example.com/a')).toBe(true);
    expect(isSafeHref('http://example.com')).toBe(true);
  });

  it('拒绝危险协议', () => {
    expect(isSafeHref('javascript:alert(1)')).toBe(false);
    expect(isSafeHref('data:text/html,<script>alert(1)</script>')).toBe(false);
    expect(isSafeHref('file:///etc/passwd')).toBe(false);
    expect(isSafeHref('')).toBe(false);
  });

  it('拒绝带凭据的链接', () => {
    expect(isSafeHref('https://user:pass@evil.com')).toBe(false);
  });
});

describe('parseMarkdown 基础结构', () => {
  it('段落与加粗', () => {
    const nodes = parseMarkdown('你的**血压**偏高');
    expect(nodes).toHaveLength(1);
    expect(nodes[0].type).toBe('paragraph');
    const text = nodesToText(nodes);
    expect(text).toContain('血压');
    const hasStrong = JSON.stringify(nodes).includes('"strong"');
    expect(hasStrong).toBe(true);
  });

  it('标题层级被限制在 1-6', () => {
    const nodes = parseMarkdown('####### 七级标题');
    const heading = nodes.find((n) => n.type === 'heading');
    if (heading && heading.type === 'heading') {
      expect(heading.depth).toBeLessThanOrEqual(6);
      expect(heading.depth).toBeGreaterThanOrEqual(1);
    }
  });

  it('无序列表', () => {
    const nodes = parseMarkdown('- 第一项\n- 第二项');
    const list = nodes.find((n) => n.type === 'list');
    expect(list).toBeTruthy();
    if (list && list.type === 'list') {
      expect(list.ordered).toBe(false);
      expect(list.items).toHaveLength(2);
    }
  });

  it('有序列表', () => {
    const nodes = parseMarkdown('1. 甲\n2. 乙');
    const list = nodes.find((n) => n.type === 'list');
    if (list && list.type === 'list') {
      expect(list.ordered).toBe(true);
    }
  });

  it('表格', () => {
    const md = '| 指标 | 值 |\n|---|---|\n| 血糖 | 5.6 |\n| 血压 | 130/85 |';
    const nodes = parseMarkdown(md);
    const table = nodes.find((n) => n.type === 'table');
    expect(table).toBeTruthy();
    if (table && table.type === 'table') {
      expect(table.header).toHaveLength(2);
      expect(table.rows).toHaveLength(2);
      expect(nodesToText([table])).toContain('5.6');
    }
  });

  it('代码块', () => {
    const nodes = parseMarkdown('```\nconst a = 1;\n```');
    const code = nodes.find((n) => n.type === 'code');
    expect(code).toBeTruthy();
    if (code && code.type === 'code') {
      expect(code.value).toContain('const a = 1');
    }
  });

  it('行内代码', () => {
    const nodes = parseMarkdown('请检查 `HbA1c` 数值');
    expect(JSON.stringify(nodes)).toContain('codespan');
  });

  it('引用块', () => {
    const nodes = parseMarkdown('> 这是一段引用');
    expect(nodes.some((n) => n.type === 'blockquote')).toBe(true);
  });

  it('分隔线', () => {
    const nodes = parseMarkdown('上\n\n---\n\n下');
    expect(nodes.some((n) => n.type === 'hr')).toBe(true);
  });
});

describe('XSS 防护', () => {
  it('原始 script 标签降级为纯文本', () => {
    const nodes = parseMarkdown('<script>alert(1)</script>');
    expect(JSON.stringify(nodes)).not.toContain('"link"');
    expect(nodesToText(nodes)).toContain('script');
  });

  it('javascript: 链接被降级为纯文本', () => {
    const nodes = parseMarkdown('[点我](javascript:alert(1))');
    const json = JSON.stringify(nodes);
    expect(json).not.toContain('javascript:');
    expect(json).not.toContain('"type":"link"');
    expect(nodesToText(nodes)).toContain('点我');
  });

  it('img 不产生图片节点', () => {
    const nodes = parseMarkdown('![x](https://evil.example/p.png)');
    const json = JSON.stringify(nodes);
    expect(json).not.toContain('"image"');
    expect(json).not.toContain('evil.example');
  });

  it('img onerror 注入不生效', () => {
    const nodes = parseMarkdown('<img src=x onerror=alert(1)>');
    // 关键：不得产生 image 节点，也不得产生任何可执行属性；
    // 原始标记只能作为纯文本 value 存在（React 渲染文本会自动转义）。
    const json = JSON.stringify(nodes);
    expect(json).not.toContain('"image"');
    expect(json).not.toContain('"href"');
    expect(nodesToText(nodes)).toContain('<img');
  });

  it('合法链接被保留', () => {
    const nodes = parseMarkdown('见 [指南](https://example.org/g)');
    expect(JSON.stringify(nodes)).toContain('https://example.org/g');
  });
});

describe('裸 URL 不吞后续内容（真实缺陷回归）', () => {
  it('URL 后接中文标点与加粗时，正文必须保留', () => {
    // 复现模型真实输出：URL 紧跟 。） 然后还有加粗内容
    const md =
      '（来源：维基百科·中風 https://zh.wikipedia.org/wiki/中風）。但**饮食不能替代降脂药物**，尤其家族性患者。';
    const nodes = parseMarkdown(md);
    const text = nodesToText(nodes);
    assertTextKept(text);
  });

  function assertTextKept(text: string) {
    // 关键断言：不应把后续正文吞进链接
    expect(text).toContain('饮食不能替代降脂药物');
    expect(text).toContain('尤其家族性患者');
    // URL 本身应保留
    expect(text).toContain('https://zh.wikipedia.org/wiki/中風');
  }

  it('URL 后的加粗应被渲染为 strong 节点', () => {
    const md = '见 https://example.com/a 。**重要提示**内容';
    const nodes = parseMarkdown(md);
    expect(JSON.stringify(nodes)).toContain('"strong"');
    expect(nodesToText(nodes)).toContain('重要提示');
  });

  it('裸 URL 被转成链接节点', () => {
    const nodes = parseMarkdown('参考 https://example.org/x 结尾');
    const json = JSON.stringify(nodes);
    expect(json).toContain('"type":"link"');
    expect(json).toContain('https://example.org/x');
    expect(nodesToText(nodes)).toContain('结尾');
  });

  it('URL 后紧跟英文括号时不吞括号后内容', () => {
    const nodes = parseMarkdown('见 https://e.com/a(b) after');
    expect(nodesToText(nodes)).toContain('after');
  });

  it('危险协议裸文本不生成链接', () => {
    const nodes = parseMarkdown('javascript:alert(1) 后面还有内容');
    expect(JSON.stringify(nodes)).not.toContain('"type":"link"');
    expect(nodesToText(nodes)).toContain('后面还有内容');
  });

  it('多个 URL 各自成链且文本完整', () => {
    const nodes = parseMarkdown('A https://a.com B https://b.com C');
    const json = JSON.stringify(nodes);
    expect((json.match(/"type":"link"/g) ?? []).length).toBe(2);
    expect(nodesToText(nodes)).toContain('C');
  });

  it('URL 后的中文逗号不被吞进链接', () => {
    const nodes = parseMarkdown('见 https://e.com/x，然后继续说明');
    const json = JSON.stringify(nodes);
    expect(json).not.toContain('x，然后');
    expect(nodesToText(nodes)).toContain('然后继续说明');
  });
});

describe('健壮性', () => {
  it('空文本返回空数组', () => {
    expect(parseMarkdown('')).toEqual([]);
    expect(parseMarkdown('   ')).toEqual([]);
  });

  it('引用标记原样保留（交由 CitationTag 处理）', () => {
    const nodes = parseMarkdown('血压偏高[依据 1：2021 化验单]');
    expect(nodesToText(nodes)).toContain('[依据 1：2021 化验单]');
  });

  it('深度嵌套不崩溃', () => {
    const md = '> '.repeat(20) + '深层引用';
    expect(() => parseMarkdown(md)).not.toThrow();
  });

  it('超长文本被节点上限截断而不卡死', () => {
    const md = Array.from({ length: 2000 }, (_, i) => `第 ${i} 段`).join('\n\n');
    const start = Date.now();
    const nodes = parseMarkdown(md);
    expect(Date.now() - start).toBeLessThan(5000);
    expect(nodes.length).toBeGreaterThan(0);
  });
});
