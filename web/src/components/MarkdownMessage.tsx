/**
 * 问诊消息 Markdown 渲染（F-09 富文本，§6.9）。
 *
 * 关键要求：
 * 1. 不使用 dangerouslySetInnerHTML（XSS 从架构上排除）。
 * 2. 引用标记 `[依据 N：标签]` 仍必须可点击 → 文本叶子节点交给 CitationTag 处理。
 * 3. 表格外层横向滚动，避免移动端溢出。
 */

import { Fragment, useMemo } from 'react';
import CitationTag from './CitationTag';
import { parseMarkdown, type MdNode } from './markdownTokens';

export interface MarkdownMessageProps {
  text: string;
  onCite?: (label: string) => void;
}

function renderInline(nodes: MdNode[], onCite?: (label: string) => void, keyPrefix = 'i') {
  return nodes.map((node, index) => {
    const key = `${keyPrefix}-${index}`;
    switch (node.type) {
      case 'text':
        return <CitationTag key={key} text={node.value} onCite={onCite} />;
      case 'strong':
        return <strong key={key}>{renderInline(node.children, onCite, key)}</strong>;
      case 'em':
        return <em key={key}>{renderInline(node.children, onCite, key)}</em>;
      case 'del':
        return <del key={key}>{renderInline(node.children, onCite, key)}</del>;
      case 'codespan':
        return (
          <code key={key} className="md-code-inline">
            {node.value}
          </code>
        );
      case 'link':
        return (
          <a key={key} href={node.href} target="_blank" rel="noopener noreferrer">
            {renderInline(node.children, onCite, key)}
          </a>
        );
      case 'space':
        return <Fragment key={key}>{' '}</Fragment>;
      default:
        // 内联位置遇到块级节点：尽量渲染其文本子节点，避免丢内容
        if ('children' in node) {
          return <Fragment key={key}>{renderInline(node.children, onCite, key)}</Fragment>;
        }
        return null;
    }
  });
}

function renderBlocks(nodes: MdNode[], onCite?: (label: string) => void, keyPrefix = 'b') {
  return nodes.map((node, index) => {
    const key = `${keyPrefix}-${index}`;
    switch (node.type) {
      case 'space':
        return null;
      case 'paragraph':
        return <p key={key}>{renderInline(node.children, onCite, key)}</p>;
      case 'heading': {
        const Tag = (`h${node.depth}` as unknown) as 'h1';
        return (
          <Tag key={key} className="md-heading">
            {renderInline(node.children, onCite, key)}
          </Tag>
        );
      }
      case 'code':
        return (
          <pre key={key} className="md-code-block">
            <code>{node.value}</code>
          </pre>
        );
      case 'blockquote':
        return (
          <blockquote key={key} className="md-quote">
            {renderBlocks(node.children, onCite, key)}
          </blockquote>
        );
      case 'hr':
        return <hr key={key} className="md-hr" />;
      case 'list': {
        const Tag = node.ordered ? 'ol' : 'ul';
        return (
          <Tag key={key} className="md-list">
            {node.items.map((item, itemIndex) => {
              const itemKey = `${key}-${itemIndex}`;
              const hasBlock = item.some(
                (child) => child.type === 'list' || child.type === 'code',
              );
              return (
                <li key={itemKey}>
                  {hasBlock
                    ? renderBlocks(item, onCite, itemKey)
                    : renderInline(item, onCite, itemKey)}
                </li>
              );
            })}
          </Tag>
        );
      }
      case 'table':
        return (
          <div key={key} className="md-table-wrap">
            <table className="md-table">
              <thead>
                <tr>
                  {node.header.map((cell, cellIndex) => (
                    <th key={`${key}-h-${cellIndex}`}>
                      {renderInline(cell, onCite, `${key}-h-${cellIndex}`)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {node.rows.map((row, rowIndex) => (
                  <tr key={`${key}-r-${rowIndex}`}>
                    {row.map((cell, cellIndex) => (
                      <td key={`${key}-r-${rowIndex}-${cellIndex}`}>
                        {renderInline(cell, onCite, `${key}-r-${rowIndex}-${cellIndex}`)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      default:
        if ('children' in node) {
          return (
            <p key={key}>{renderInline(node.children, onCite, key)}</p>
          );
        }
        return null;
    }
  });
}

/** 渲染助手回答的 Markdown 内容。 */
export default function MarkdownMessage({ text, onCite }: MarkdownMessageProps) {
  const nodes = useMemo(() => parseMarkdown(text), [text]);
  if (nodes.length === 0) return null;
  return <div className="md-body">{renderBlocks(nodes, onCite)}</div>;
}
