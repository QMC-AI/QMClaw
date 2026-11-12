/**
 * Qwf Info Panel - 工作流级 `.qwf` 信息展示
 *
 * 画布打开一个 `.qwf`（导入 / 服务端示例 / 存档）之后，`.qwf` 里不只是
 * 一串语句，还有工作流级的字段：
 *
 *   meta / env / context / defaults / budget / policy
 *
 * 这些字段在画布上没有对应控件，但导出时必须原样带回去（否则往返不等价）。
 * 这个面板把它们全部摊开显示，让用户能确认"这个 `.qwf` 的完整信息"，
 * 同时把两处可能丢语义的地方显式警告出来：
 *
 *   1. 画布表达不了的语句（repeat / if / call…）—— 以灰节点保留原文；
 *   2. 节点上没被画布编辑的 `.qwf` 字段——以原语句（dsl.stmt）为准。
 */

"use client";

import { useState } from 'react';
import { useWorkflowStore } from '../../store/workflowStore';

interface Props {
  onClose: () => void;
}

/** 工作流级字段在 `.qwf` 里的键名与说明 */
const WORKFLOW_FIELDS: Array<{ key: string; label: string; hint: string }> = [
  { key: 'meta', label: 'meta', hint: '名字 / 版本 / 作者 / 描述' },
  { key: 'env', label: 'env', hint: '环境变量（$env.xxx 引用来源）' },
  { key: 'context', label: 'context', hint: '全局上下文变量' },
  { key: 'defaults', label: 'defaults', hint: '语句默认值' },
  { key: 'budget', label: 'budget', hint: '预算（maxWrites / maxRuntime…）' },
  { key: 'policy', label: 'policy', hint: '策略（门禁失败后的行为等）' },
];

const Section: React.FC<{ title: string; hint?: string; children: React.ReactNode }> = ({
  title,
  hint,
  children,
}) => (
  <div style={{ marginBottom: '12px' }}>
    <div style={{ display: 'flex', alignItems: 'baseline', gap: '6px', marginBottom: '4px' }}>
      <span style={{ fontSize: '11px', fontWeight: 700, color: '#38bdf8' }}>{title}</span>
      {hint && <span style={{ fontSize: '9px', color: '#475569' }}>{hint}</span>}
    </div>
    {children}
  </div>
);

const JsonBlock: React.FC<{ value: unknown }> = ({ value }) => {
  const text =
    value === undefined || value === null
      ? '（未设置）'
      : JSON.stringify(value, null, 2);
  const empty = value === undefined || value === null ||
    (typeof value === 'object' && Object.keys(value as object).length === 0);
  return (
    <pre
      style={{
        margin: 0,
        padding: '8px',
        background: '#1e293b',
        border: `1px solid ${empty ? '#1e293b' : '#334155'}`,
        borderRadius: '6px',
        color: empty ? '#475569' : '#e2e8f0',
        fontFamily: 'monospace',
        fontSize: '10px',
        lineHeight: 1.45,
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-all',
        maxHeight: '260px',
        overflow: 'auto',
      }}
    >
      {text}
    </pre>
  );
};

export default function QwfInfoPanel({ onClose }: Props) {
  const dsl = useWorkflowStore((state) => state.dsl);
  const workflowName = useWorkflowStore((state) => state.workflowName);
  const nodeCount = useWorkflowStore((state) => state.nodes.length);
  const [showRaw, setShowRaw] = useState(false);

  const wf = (dsl.workflow || {}) as Record<string, unknown>;
  const hasRaw = typeof dsl.qwf === 'string' && dsl.qwf.trim().length > 0;
  const sourceLabel =
    dsl.source === 'example'
      ? '服务端示例'
      : dsl.source === 'import'
        ? '本地导入'
        : dsl.source === 'saved'
          ? '服务端存档'
          : dsl.source || '未知来源';

  return (
    <div
      style={{
        position: 'absolute',
        top: '16px',
        right: '16px',
        width: '380px',
        maxHeight: 'calc(100% - 32px)',
        display: 'flex',
        flexDirection: 'column',
        background: '#0f172a',
        border: '1px solid #1e293b',
        borderRadius: '12px',
        zIndex: 30,
        boxShadow: '0 8px 32px rgba(0,0,0,0.45)',
        overflow: 'hidden',
      }}
    >
      {/* Header */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '12px 16px',
          background: '#1e293b',
          borderBottom: '1px solid #334155',
          flexShrink: 0,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span style={{ fontSize: '16px' }}>📄</span>
          <div style={{ display: 'flex', flexDirection: 'column' }}>
            <span style={{ fontSize: '12px', fontWeight: 600, color: '#e2e8f0' }}>
              .qwf 工作流信息
            </span>
            <span style={{ fontSize: '9px', color: '#64748b' }}>
              {workflowName} · {nodeCount} 个节点 · 来源：{sourceLabel}
            </span>
          </div>
        </div>
        <button
          onClick={onClose}
          style={{
            padding: '2px 8px',
            background: 'transparent',
            border: '1px solid #334155',
            borderRadius: '4px',
            color: '#64748b',
            cursor: 'pointer',
            fontSize: '12px',
          }}
        >
          ✕
        </button>
      </div>

      {/* Body */}
      <div style={{ padding: '12px 16px', overflowY: 'auto', flex: 1 }}>
        {dsl.unsupported && dsl.unsupported.length > 0 && (
          <div
            style={{
              marginBottom: '12px',
              padding: '8px 10px',
              background: '#422006',
              border: '1px solid #b45309',
              borderRadius: '6px',
              color: '#fbbf24',
              fontSize: '10px',
              lineHeight: 1.5,
            }}
          >
            <strong>⚠ 画布暂不支持 {dsl.unsupported.length} 类语句</strong>
            <div style={{ marginTop: '4px', color: '#fcd34d' }}>
              {dsl.unsupported.join(', ')}
            </div>
            <div style={{ marginTop: '4px', color: '#fde68a' }}>
              这些语句在画布上是灰节点，原始语句被完整保留，导出时会原样放回。
            </div>
          </div>
        )}

        {WORKFLOW_FIELDS.map((f) => (
          <Section key={f.key} title={f.label} hint={f.hint}>
            <JsonBlock value={wf[f.key]} />
          </Section>
        ))}

        <Section title="原始 .qwf" hint="导入 / 存档时的原文（未经画布往返）">
          {hasRaw ? (
            <>
              <button
                onClick={() => setShowRaw(!showRaw)}
                style={{
                  marginBottom: '6px',
                  padding: '3px 8px',
                  background: '#1e293b',
                  border: '1px solid #334155',
                  borderRadius: '4px',
                  color: '#94a3b8',
                  cursor: 'pointer',
                  fontSize: '10px',
                }}
              >
                {showRaw ? '收起原文' : '展开原文'}
              </button>
              {showRaw && (
                <pre
                  style={{
                    margin: 0,
                    padding: '8px',
                    background: '#1e293b',
                    borderRadius: '6px',
                    color: '#e2e8f0',
                    fontFamily: 'monospace',
                    fontSize: '10px',
                    lineHeight: 1.45,
                    whiteSpace: 'pre-wrap',
                    wordBreak: 'break-all',
                    maxHeight: '320px',
                    overflow: 'auto',
                  }}
                >
                  {dsl.qwf}
                </pre>
              )}
            </>
          ) : (
            <div style={{ fontSize: '10px', color: '#475569' }}>
              （当前画布内容不是从 `.qwf` 打开的，没有原文可看）
            </div>
          )}
        </Section>
      </div>

      {/* Footer */}
      <div
        style={{
          padding: '8px 16px',
          borderTop: '1px solid #1e293b',
          fontSize: '9px',
          color: '#475569',
          flexShrink: 0,
        }}
      >
        导出时会用画布内容重新生成 `.qwf`，并与打开时的原文做语义等价比对；
        不等价会阻止导出。
      </div>
    </div>
  );
}
