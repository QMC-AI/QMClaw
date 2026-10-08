"use client";

/**
 * Markdown Content Renderer
 *
 * Provides rich markdown rendering with:
 * - Syntax highlighting for code blocks
 * - Copy button for code blocks
 * - Styled blockquotes, links, tables
 * - Embedded image extraction from JSON syntax
 *
 * Based on hermes-hudui implementation, adapted for QMClaw theme.
 */

import { useState, useCallback } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import 'highlight.js/styles/atom-one-dark.css';

// QMClaw color scheme
const colors = {
  primary: '#38bdf8',
  bgDeep: '#0f172a',
  bgHover: '#1e293b',
  bgSurface: '#1e293b',
  border: '#334155',
  text: '#e2e8f0',
  textDim: '#64748b',
  success: '#22c55e',
  warning: '#f59e0b',
  error: '#ef4444',
};

// ── Embedded Image Extraction ──────────────────────────────────────────────────

interface EmbeddedImage {
  alt: string;
  src: string;  // The actual image data URL or path
  fullMatch: string;  // The original markdown syntax
}

/**
 * Find matching closing parenthesis for markdown image URL.
 * Properly handles nested braces in JSON.
 */
function findMatchingParen(content: string, startIdx: number): number {
  let depth = 0;
  let inString = false;
  let stringChar = '';
  let i = startIdx;

  while (i < content.length) {
    const char = content[i];

    if (!inString) {
      if (char === '"' || char === "'" || char === '`') {
        inString = true;
        stringChar = char;
      } else if (char === '(') {
        depth++;
      } else if (char === ')') {
        if (depth === 0) {
          return i;
        }
        depth--;
      }
    } else {
      if (char === stringChar && content[i - 1] !== '\\') {
        inString = false;
      }
    }
    i++;
  }
  return -1;  // Not found
}

/**
 * Extract embedded images from content.
 * Handles formats like: ![alt]({"success": true, "image": "data:image/png;base64,..."})
 */
function extractEmbeddedImages(content: string): { images: EmbeddedImage[]; cleanedContent: string } {
  const images: EmbeddedImage[] = [];

  // Find all markdown image patterns: ![alt](url)
  const imgRegex = /!\[([^\]]*)\]/g;
  let match;
  let lastIndex = 0;

  // Reset regex state
  imgRegex.lastIndex = 0;

  while ((match = imgRegex.exec(content)) !== null) {
    const alt = match[1] || 'image';
    const bracketEnd = match.index + match[0].length;

    // Must be followed by (url)
    if (content[bracketEnd] !== '(') continue;

    // Find the matching closing parenthesis
    const urlStart = bracketEnd + 1;
    const urlEnd = findMatchingParen(content, urlStart);
    if (urlEnd === -1) continue;

    const url = content.substring(urlStart, urlEnd);
    const fullMatch = content.substring(match.index, urlEnd + 1);

    // Try to parse URL as JSON
    try {
      const json = JSON.parse(url);
      let src = '';

      // Extract image from various possible fields
      if (json.image) {
        src = json.image;
      } else if (json.image_url) {
        src = json.image_url;
      } else if (json.plotUrl) {
        src = json.plotUrl;
      } else if (json.success && json.data?.image) {
        src = json.data.image;
      } else if (json.output) {
        // Handle terminal output that contains image filenames
        // e.g. {"output": "0d127bbe75fa7a04.png\n...", ...}
        // Don't treat output as single image, skip
        continue;
      }

      if (src) {
        images.push({ alt, src, fullMatch });
      }
    } catch {
      // Not valid JSON - check if it's a direct URL
      if (url.startsWith('data:image/') || url.startsWith('/plots/') || url.startsWith('http')) {
        images.push({ alt, src: url, fullMatch });
      }
    }
  }

  // Remove all embedded image syntax from content
  let cleanedContent = content;
  for (const img of images) {
    cleanedContent = cleanedContent.replace(img.fullMatch, '');
  }
  // Clean up any double newlines created by removal
  cleanedContent = cleanedContent.replace(/\n{3,}/g, '\n\n').trim();

  return { images, cleanedContent };
}

// ── Copy Button Component ───────────────────────────────────────────────────────

interface CopyButtonProps {
  text: string;
}

function CopyButton({ text }: CopyButtonProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Fallback for environments without clipboard API
      const el = document.createElement('textarea');
      el.value = text;
      document.body.appendChild(el);
      el.select();
      document.execCommand('copy');
      document.body.removeChild(el);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  return (
    <button
      onClick={handleCopy}
      className="absolute top-2 right-2 px-2 py-0.5 text-[11px] opacity-0 group-hover:opacity-100 transition-opacity cursor-pointer"
      style={{
        background: copied ? colors.success : colors.bgHover,
        color: copied ? colors.bgDeep : colors.textDim,
        border: `1px solid ${colors.border}`,
        borderRadius: '0.25rem',
      }}
    >
      {copied ? 'Copied!' : 'Copy'}
    </button>
  );
}

// ── Extract raw text from code node ───────────────────────────────────────────

function extractRawText(node: any): string {
  if (!node) return '';

  if (node.type === 'text') {
    return node.value || '';
  }

  if (node.children) {
    return node.children.map(extractRawText).join('');
  }

  return '';
}

// ── Image Display Component ────────────────────────────────────────────────────

interface ImageDisplayProps {
  img: EmbeddedImage;
}

function ImageDisplay({ img }: ImageDisplayProps) {
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState(false);

  const openInNewTab = useCallback(() => {
    if (img.src.startsWith('data:')) {
      const arr = img.src.split(',');
      const mime = arr[0].match(/:(.*?);/)?.[1] || 'image/png';
      const bstr = atob(arr[1]);
      let n = bstr.length;
      const u8arr = new Uint8Array(n);
      while (n--) u8arr[n] = bstr.charCodeAt(n);
      const blob = new Blob([u8arr], { type: mime });
      const url = URL.createObjectURL(blob);
      window.open(url, '_blank');
    } else {
      window.open(img.src, '_blank');
    }
  }, [img.src]);

  const downloadImage = useCallback(() => {
    try {
      let blob: Blob;
      let filename = `${img.alt || 'image'}.png`;

      if (img.src.startsWith('data:')) {
        const arr = img.src.split(',');
        const mime = arr[0].match(/:(.*?);/)?.[1] || 'image/png';
        const bstr = atob(arr[1]);
        let n = bstr.length;
        const u8arr = new Uint8Array(n);
        while (n--) u8arr[n] = bstr.charCodeAt(n);
        blob = new Blob([u8arr], { type: mime });
      } else if (img.src.startsWith('http') || img.src.startsWith('/')) {
        fetch(img.src)
          .then(res => res.blob())
          .then(blob => {
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
          });
        return;
      } else {
        return;
      }

      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (e) {
      console.error('Failed to download image:', e);
    }
  }, [img]);

  return (
    <div style={{
      background: colors.bgDeep,
      borderRadius: '0.5rem',
      padding: '0.5rem',
      marginTop: '0.5rem',
      marginBottom: '0.5rem',
      border: `1px solid ${error ? colors.error : colors.border}`,
    }}>
      <div style={{ color: colors.textDim, fontSize: '0.65rem', marginBottom: '0.25rem' }}>
        🖼️ {img.alt || "image"}
        {error && <span style={{ color: colors.error, marginLeft: '0.5rem' }}>❌ 加载失败</span>}
      </div>

      {!loaded && !error && (
        <div style={{
          height: '150px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: colors.bgHover,
          borderRadius: '0.25rem',
          color: colors.textDim,
          fontSize: '0.75rem',
        }}>
          加载中...
        </div>
      )}

      {error && (
        <div style={{
          padding: '0.75rem',
          background: colors.bgHover,
          borderRadius: '0.25rem',
          color: colors.error,
          fontSize: '0.7rem',
        }}>
          <div>⚠️ 图片加载失败</div>
          <div style={{ color: colors.textDim, marginTop: '0.25rem', wordBreak: 'break-all' }}>
            URL: {img.src.substring(0, 100)}{img.src.length > 100 ? '...' : ''}
          </div>
        </div>
      )}

      <img
        src={img.src}
        alt={img.alt || "image"}
        style={{
          maxWidth: '100%',
          maxHeight: '300px',
          borderRadius: '0.25rem',
          display: loaded ? 'block' : 'none',
          cursor: 'zoom-in',
        }}
        onLoad={() => setLoaded(true)}
        onError={() => setError(true)}
        onClick={openInNewTab}
      />

      <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.5rem' }}>
        <button
          onClick={downloadImage}
          style={{
            padding: '0.25rem 0.5rem',
            background: colors.bgHover,
            border: `1px solid ${colors.border}`,
            borderRadius: '0.25rem',
            color: colors.primary,
            fontSize: '0.7rem',
            cursor: 'pointer',
          }}
        >
          💾 Download
        </button>
        <button
          onClick={openInNewTab}
          style={{
            padding: '0.25rem 0.5rem',
            background: colors.bgHover,
            border: `1px solid ${colors.border}`,
            borderRadius: '0.25rem',
            color: colors.primary,
            fontSize: '0.7rem',
            cursor: 'pointer',
          }}
        >
          🔍 Full View
        </button>
      </div>
    </div>
  );
}

// ── MarkdownContent Component ──────────────────────────────────────────────────

interface MarkdownContentProps {
  content: string;
  className?: string;
}

export default function MarkdownContent({ content, className = '' }: MarkdownContentProps) {
  // Extract embedded images from content (handles JSON-style embedded images)
  const { images, cleanedContent } = extractEmbeddedImages(content);

  return (
    <div className={`prose-hud ${className}`}>
      {/* Render extracted images above the text */}
      {images.length > 0 && (
        <div style={{ marginBottom: '0.5rem' }}>
          {images.map((img, idx) => (
            <ImageDisplay key={idx} img={img} />
          ))}
        </div>
      )}

      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeHighlight]}
        components={{
          // Inline code — styled, no copy button
          code({ className: codeClass, children, ...props }) {
            const isBlock = codeClass?.includes('language-');
            if (isBlock) {
              return (
                <code
                  className={codeClass}
                  style={{
                    background: colors.bgDeep,
                    color: colors.primary,
                    padding: '1px 4px',
                    borderRadius: '0.2rem',
                    fontSize: '0.85em',
                    fontFamily: 'monospace',
                  }}
                  {...props}
                >
                  {children}
                </code>
              );
            }
            return (
              <code
                style={{
                  background: colors.bgDeep,
                  color: colors.primary,
                  padding: '1px 4px',
                  borderRadius: '0.2rem',
                  fontSize: '0.85em',
                  fontFamily: 'monospace',
                }}
                {...props}
              >
                {children}
              </code>
            );
          },

          // Pre block — wraps code block, provides copy button via group hover
          pre({ children, node, ...props }) {
            // Extract raw text from AST for clipboard
            const rawText = extractRawText((node as any)?.children?.[0]);

            return (
              <div className="relative group my-2">
                <CopyButton text={rawText} />
                <pre
                  style={{
                    background: colors.bgDeep,
                    border: `1px solid ${colors.border}`,
                    borderRadius: '0.25rem',
                    padding: '0.75rem',
                    overflowX: 'auto',
                    fontSize: '13px',
                    margin: 0,
                    fontFamily: 'monospace',
                  }}
                  {...props}
                >
                  {children}
                </pre>
              </div>
            );
          },

          // Style blockquotes
          blockquote({ children, ...props }) {
            return (
              <blockquote
                style={{
                  borderLeft: `3px solid ${colors.primary}`,
                  paddingLeft: '0.75rem',
                  margin: '0.5rem 0',
                  color: colors.textDim,
                  fontStyle: 'italic',
                }}
                {...props}
              >
                {children}
              </blockquote>
            );
          },

          // Style links
          a({ children, href, ...props }) {
            return (
              <a
                href={href}
                target="_blank"
                rel="noopener noreferrer"
                style={{ color: colors.primary, textDecoration: 'underline' }}
                {...props}
              >
                {children}
              </a>
            );
          },

          // Style tables
          table({ children, ...props }) {
            return (
              <div style={{ overflowX: 'auto', margin: '0.5rem 0' }}>
                <table
                  style={{
                    borderCollapse: 'collapse',
                    width: '100%',
                    fontSize: '13px',
                  }}
                  {...props}
                >
                  {children}
                </table>
              </div>
            );
          },

          th({ children, ...props }) {
            return (
              <th
                style={{
                  border: `1px solid ${colors.border}`,
                  padding: '4px 8px',
                  background: colors.bgSurface,
                  color: colors.primary,
                  textAlign: 'left',
                }}
                {...props}
              >
                {children}
              </th>
            );
          },

          td({ children, ...props }) {
            return (
              <td
                style={{
                  border: `1px solid ${colors.border}`,
                  padding: '4px 8px',
                }}
                {...props}
              >
                {children}
              </td>
            );
          },

          // Style lists
          ul({ children, ...props }) {
            return (
              <ul style={{ margin: '0.5rem 0', paddingLeft: '1.5rem' }} {...props}>
                {children}
              </ul>
            );
          },

          ol({ children, ...props }) {
            return (
              <ol style={{ margin: '0.5rem 0', paddingLeft: '1.5rem' }} {...props}>
                {children}
              </ol>
            );
          },

          li({ children, ...props }) {
            return (
              <li style={{ margin: '0.25rem 0' }} {...props}>
                {children}
              </li>
            );
          },

          // Style headings
          h1({ children, ...props }) {
            return (
              <h1 style={{ fontSize: '1.5rem', fontWeight: 'bold', margin: '1rem 0 0.5rem', color: colors.text }} {...props}>
                {children}
              </h1>
            );
          },

          h2({ children, ...props }) {
            return (
              <h2 style={{ fontSize: '1.25rem', fontWeight: 'bold', margin: '0.75rem 0 0.5rem', color: colors.text }} {...props}>
                {children}
              </h2>
            );
          },

          h3({ children, ...props }) {
            return (
              <h3 style={{ fontSize: '1.1rem', fontWeight: 'bold', margin: '0.5rem 0 0.25rem', color: colors.text }} {...props}>
                {children}
              </h3>
            );
          },

          // Style paragraphs
          p({ children, ...props }) {
            return (
              <p style={{ margin: '0.5rem 0' }} {...props}>
                {children}
              </p>
            );
          },

          // Style horizontal rule
          hr({ ...props }) {
            return (
              <hr style={{ borderColor: colors.border, margin: '1rem 0' }} {...props} />
            );
          },
        }}
      >
        {cleanedContent}
      </ReactMarkdown>
    </div>
  );
}
