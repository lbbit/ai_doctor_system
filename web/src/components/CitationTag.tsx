import { useMemo } from 'react';

/** 引用标记正则（与后端 citations.py 一致：容忍编号与全半角冒号）。 */
export const CITATION_RE = /\[依据\s*(\d*)\s*[:：]\s*([^\]\n]{1,200})\]/g;

export interface ParsedCitation {
  marker: string;
  label: string;
}

/** 从回答文本解析引用标记（供渲染与单测复用）。 */
export function parseCitations(text: string): ParsedCitation[] {
  const out: ParsedCitation[] = [];
  for (const m of text.matchAll(CITATION_RE)) {
    out.push({ marker: m[0], label: m[2].trim() });
  }
  return out;
}

export interface CitationTagProps {
  text: string;
  onCite?: (label: string) => void;
}

/** F-10 引用标记渲染：[依据：标签] → 可点击 chip（AC-10.1）。 */
export default function CitationTag({ text, onCite }: CitationTagProps) {
  const parts = useMemo(() => {
    const result: Array<{ type: 'text' | 'cite'; value: string }> = [];
    let last = 0;
    for (const m of text.matchAll(CITATION_RE)) {
      const idx = m.index ?? 0;
      if (idx > last) result.push({ type: 'text', value: text.slice(last, idx) });
      result.push({ type: 'cite', value: m[2].trim() });
      last = idx + m[0].length;
    }
    if (last < text.length) result.push({ type: 'text', value: text.slice(last) });
    return result;
  }, [text]);

  return (
    <>
      {parts.map((p, i) =>
        p.type === 'cite' ? (
          <button
            key={i}
            type="button"
            className="citation-tag"
            title="查看原始报告"
            onClick={() => onCite?.(p.value)}
          >
            依据：{p.value}
          </button>
        ) : (
          <span key={i}>{p.value}</span>
        ),
      )}
    </>
  );
}
