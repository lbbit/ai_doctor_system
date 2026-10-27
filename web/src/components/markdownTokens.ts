/**
 * Markdown token 规范化（纯函数，可单测）。
 *
 * 设计目标（安全第一）：
 * - 只保留白名单 token；未知类型一律降级为纯文本，永不丢失用户可见内容。
 * - 链接只允许 http/https，且去除内嵌凭据；其他协议降级为纯文本。
 * - **不使用 dangerouslySetInnerHTML**，从架构上杜绝 XSS，
 *   因此不需要依赖 DOMPurify 之类运行时消毒。
 */

import { marked, type Token, type Tokens } from 'marked';

export type MdNode =
  | { type: 'paragraph'; children: MdNode[] }
  | { type: 'heading'; depth: number; children: MdNode[] }
  | { type: 'strong'; children: MdNode[] }
  | { type: 'em'; children: MdNode[] }
  | { type: 'del'; children: MdNode[] }
  | { type: 'text'; value: string }
  | { type: 'code'; value: string; lang?: string }
  | { type: 'codespan'; value: string }
  | { type: 'link'; href: string; children: MdNode[] }
  | { type: 'list'; ordered: boolean; items: MdNode[][] }
  | { type: 'blockquote'; children: MdNode[] }
  | { type: 'hr' }
  | {
      type: 'table';
      header: MdNode[][];
      rows: MdNode[][][];
    }
  | { type: 'space' };

/** 只允许 http/https 且无内嵌凭据。 */
export function isSafeHref(href: string): boolean {
  if (!href) return false;
  const text = href.trim();
  if (!text) return false;
  try {
    const url = new URL(text, 'https://placeholder.invalid');
    if (url.protocol !== 'http:' && url.protocol !== 'https:') return false;
    if (url.username || url.password) return false;
    return true;
  } catch {
    return false;
  }
}

const MAX_NODES = 4000;

/**
 * 剥离副本中的机器锚点（`<!-- source:page=N -->` / `<!-- block:... -->`）。
 *
 * 这些注释是给"点击引用跳到对应页"用的定位标记，不是给人读的正文；
 * 渲染前去掉，避免用户看到一串 HTML 注释。保留其余内容原样。
 */
export function stripSourceAnchors(text: string): string {
  if (!text) return text;
  return text
    .replace(/<!--\s*source:page=\d+\s*-->/g, '')
    .replace(/<!--\s*block:[\w:.-]+\s*-->/g, '')
    .replace(/\n{3,}/g, '\n\n');
}

/**
 * 裸 URL 正则：只匹配到"中文标点 / 空白 / 右括号"为止。
 *
 * 关键点：**不包含** `）` `。` `，` `；` `：` `！` `？` 等中文标点，
 * 也不包含 `)` `,` `.` `;` `:` `!` `?`。这样
 * `https://x/中風）。但**不能替代**` 只会把 URL 部分转成链接，
 * 后面的 `）。但**不能替代**` 保留为正文（与 GFM 的贪婪行为相反）。
 */
const BARE_URL_RE = /(?:https?:\/\/)[^\s\u3000-\u303f\uff00-\uffef()<>"'`]+/g;

/** 把纯文本中的裸 URL 切成 [文本, 链接, 文本, ...] 节点数组。 */
function linkifyBareUrl(value: string): MdNode[] {
  const parts: MdNode[] = [];
  let last = 0;
  for (const match of value.matchAll(BARE_URL_RE)) {
    const url = match[0].replace(/[.,;:!?]+$/, '');
    const idx = match.index ?? 0;
    if (idx > last) parts.push({ type: 'text', value: value.slice(last, idx) });
    if (isSafeHref(url)) {
      parts.push({ type: 'link', href: url, children: [{ type: 'text', value: url }] });
    } else {
      parts.push({ type: 'text', value: url });
    }
    last = idx + url.length;
  }
  if (last < value.length) parts.push({ type: 'text', value: value.slice(last) });
  return parts.length ? parts : [{ type: 'text', value }];
}

/**
 * 链接内部专用：不做裸 URL 提取，避免 `[url](url)` 产生嵌套 link。
 * 仍保留 strong/em/codespan 等内联样式。
 */
function normalizeInlineNoAutolink(token: Token | string): MdNode[] {
  if (typeof token === 'string') return [{ type: 'text', value: token }];
  const t = token as Tokens.Generic;
  switch (t.type) {
    case 'text': {
      const inner = (t as Tokens.Text).tokens;
      if (inner && inner.length > 0) {
        return inner.flatMap(normalizeInlineNoAutolink);
      }
      return [{ type: 'text', value: t.raw ?? '' }];
    }
    case 'strong': {
      const s = t as Tokens.Strong;
      return [
        {
          type: 'strong',
          children: (s.tokens ?? []).flatMap(normalizeInlineNoAutolink),
        },
      ];
    }
    case 'em': {
      const s = t as Tokens.Em;
      return [
        { type: 'em', children: (s.tokens ?? []).flatMap(normalizeInlineNoAutolink) },
      ];
    }
    case 'codespan':
      return [{ type: 'codespan', value: (t as Tokens.Codespan).text ?? '' }];
    case 'escape':
      return [{ type: 'text', value: (t as Tokens.Escape).text ?? '' }];
    default:
      return [{ type: 'text', value: t.raw ?? '' }];
  }
}

function normalizeInline(token: Token | string): MdNode[] {
  if (typeof token === 'string') return [{ type: 'text', value: token }];
  const t = token as Tokens.Generic;
  switch (t.type) {
    case 'text': {
      const inner = (t as Tokens.Text).tokens;
      if (inner && inner.length > 0) {
        // 文本节点内部可能还含有 strong/em 等子 token
        return inner.flatMap(normalizeInline);
      }
      return linkifyBareUrl(t.raw ?? '');
    }
    case 'escape':
      return [{ type: 'text', value: (t as Tokens.Escape).text ?? '' }];
    case 'strong': {
      const s = t as Tokens.Strong;
      return [{ type: 'strong', children: (s.tokens ?? []).flatMap(normalizeInline) }];
    }
    case 'em': {
      const s = t as Tokens.Em;
      return [{ type: 'em', children: (s.tokens ?? []).flatMap(normalizeInline) }];
    }
    case 'del': {
      const s = t as Tokens.Del;
      return [{ type: 'del', children: (s.tokens ?? []).flatMap(normalizeInline) }];
    }
    case 'codespan':
      return [{ type: 'codespan', value: (t as Tokens.Codespan).text ?? '' }];
    case 'br':
      return [{ type: 'text', value: '\n' }];
    case 'link': {
      const l = t as Tokens.Link;
      // 注意：链接自身的显示文本**不再**做裸 URL 提取，
      // 否则 `[https://a.com](https://a.com)` 会生成嵌套 link（数量翻倍）。
      const children = (l.tokens ?? []).flatMap((child) =>
        normalizeInlineNoAutolink(child),
      );
      if (!isSafeHref(l.href)) {
        // 不安全链接：仅保留可见文本，不带 href
        return children.length ? children : [{ type: 'text', value: l.text ?? '' }];
      }
      return [{ type: 'link', href: l.href, children }];
    }
    case 'image': {
      // 医疗回答中的外链图片不加载（隐私与安全），只显示替代文本
      const img = t as Tokens.Image;
      return [{ type: 'text', value: img.text ? `[图片：${img.text}]` : '[图片]' }];
    }
    case 'html':
      // 原始 HTML 一律按纯文本显示，绝不注入
      return [{ type: 'text', value: t.raw ?? '' }];
    default:
      return [{ type: 'text', value: t.raw ?? '' }];
  }
}

function normalizeBlocks(tokens: Token[], depth = 0): MdNode[] {
  if (depth > 8) return [];
  const out: MdNode[] = [];
  for (const token of tokens) {
    if (out.length >= MAX_NODES) break;
    const t = token as Tokens.Generic;
    switch (t.type) {
      case 'space':
        out.push({ type: 'space' });
        break;
      case 'heading': {
        const h = t as Tokens.Heading;
        out.push({
          type: 'heading',
          depth: Math.min(6, Math.max(1, h.depth || 1)),
          children: (h.tokens ?? []).flatMap(normalizeInline),
        });
        break;
      }
      case 'paragraph': {
        const p = t as Tokens.Paragraph;
        out.push({
          type: 'paragraph',
          children: (p.tokens ?? []).flatMap(normalizeInline),
        });
        break;
      }
      case 'text': {
        const inner = (t as Tokens.Text).tokens;
        if (inner && inner.length) {
          out.push({
            type: 'paragraph',
            children: inner.flatMap(normalizeInline),
          });
        } else {
          out.push({ type: 'paragraph', children: normalizeInline(token) });
        }
        break;
      }
      case 'code': {
        const c = t as Tokens.Code;
        out.push({ type: 'code', value: c.text ?? '', lang: c.lang || undefined });
        break;
      }
      case 'blockquote': {
        const b = t as Tokens.Blockquote;
        out.push({
          type: 'blockquote',
          children: normalizeBlocks(b.tokens ?? [], depth + 1),
        });
        break;
      }
      case 'hr':
        out.push({ type: 'hr' });
        break;
      case 'list': {
        const l = t as Tokens.List;
        const items: MdNode[][] = (l.items ?? []).map((item) => {
          const inner = item.tokens ?? [];
          const hasBlock = inner.some((x) => {
            const tt = x as Tokens.Generic;
            return tt.type === 'list' || tt.type === 'code' || tt.type === 'blockquote';
          });
          if (hasBlock) return normalizeBlocks(inner, depth + 1);
          return inner.flatMap(normalizeInline);
        });
        out.push({ type: 'list', ordered: Boolean(l.ordered), items });
        break;
      }
      case 'table': {
        const tb = t as Tokens.Table;
        const header = (tb.header ?? []).map((cell) =>
          (cell.tokens ?? []).flatMap(normalizeInline),
        );
        const rows = (tb.rows ?? []).map((row) =>
          row.map((cell) => (cell.tokens ?? []).flatMap(normalizeInline)),
        );
        out.push({ type: 'table', header, rows });
        break;
      }
      case 'html':
        // 块级 HTML 当纯文本段落
        out.push({
          type: 'paragraph',
          children: [{ type: 'text', value: t.raw ?? '' }],
        });
        break;
      default:
        out.push({
          type: 'paragraph',
          children: normalizeInline(token),
        });
        break;
    }
  }
  return out;
}

/**
 * 预处理：把裸 URL 转成受限的 Markdown 链接 `[url](url)`。
 *
 * 为什么必须在 lexer 之前处理：marked 的 gfm autolink 会贪婪匹配，
 * 实测 `见 https://e.com/x，然后继续说明` 会被整段吞成
 * `{"type":"link","href":"https://e.com/x，然后继续说明"}`，
 * 导致 URL 之后的所有正文消失（真实缺陷）。
 *
 * 这里先把 URL 截断到中文标点/空白/括号，再包成显式链接，
 * 之后 marked 就会按我们的边界正确处理，不再吞后续内容。
 */
function protectBareUrls(text: string): string {
  return text.replace(BARE_URL_RE, (match) => {
    const url = match.replace(/[.,;:!?]+$/, '');
    if (!isSafeHref(url)) return match;
    // 用尖括号形式显式声明链接边界（CommonMark autolink，支持中文路径）
    return `<${url}>`;
  });
}

/**
 * Markdown 文本 → 规范化节点树。
 */
export function parseMarkdown(text: string): MdNode[] {
  if (!text || !text.trim()) return [];
  try {
    const prepared = protectBareUrls(text);
    const tokens = marked.lexer(prepared, { gfm: true, breaks: true });
    return normalizeBlocks(tokens);
  } catch {
    // 解析失败时降级为纯文本，保证内容不丢
    return [{ type: 'paragraph', children: [{ type: 'text', value: text }] }];
  }
}

/** 提取节点树中的全部纯文本（用于测试与检索）。 */
export function nodesToText(nodes: MdNode[]): string {
  const parts: string[] = [];
  const walk = (list: MdNode[]) => {
    for (const node of list) {
      switch (node.type) {
        case 'text':
        case 'code':
        case 'codespan':
          parts.push(node.value);
          break;
        case 'hr':
          parts.push('---');
          break;
        case 'space':
          parts.push(' ');
          break;
        case 'table':
          node.header.forEach((cell) => walk(cell));
          node.rows.forEach((row) => row.forEach((cell) => walk(cell)));
          break;
        case 'list':
          node.items.forEach((item) => walk(item));
          break;
        default:
          if ('children' in node) walk(node.children);
          break;
      }
    }
  };
  walk(nodes);
  return parts.join('');
}
