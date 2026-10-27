/**
 * 副本机器锚点剥离（R1 T107）。
 *
 * 副本含 `<!-- source:page=N -->` / `<!-- block:... -->` 定位注释，
 * 供"点击引用跳到对应页"用；人读前必须剥离，否则用户看到一串 HTML 注释。
 */
import { describe, expect, it } from 'vitest';
import { stripSourceAnchors } from '../src/components/markdownTokens';

describe('stripSourceAnchors', () => {
  it('剥离页锚点', () => {
    const out = stripSourceAnchors('前言\n<!-- source:page=1 -->\n正文');
    expect(out).not.toContain('source:page');
    expect(out).toContain('前言');
    expect(out).toContain('正文');
  });

  it('剥离块锚点', () => {
    const out = stripSourceAnchors('<!-- block:1:b7 -->\n血糖 6.8');
    expect(out).not.toContain('block:');
    expect(out).toContain('血糖 6.8');
  });

  it('保留其它 HTML 注释（不误删非锚点内容）', () => {
    const out = stripSourceAnchors('<!-- 这是备注 -->\n正文');
    expect(out).toContain('这是备注');
  });

  it('折叠多余空行', () => {
    const out = stripSourceAnchors('a\n\n\n\nb');
    expect(out).toBe('a\n\nb');
  });

  it('空输入原样返回', () => {
    expect(stripSourceAnchors('')).toBe('');
  });
});
