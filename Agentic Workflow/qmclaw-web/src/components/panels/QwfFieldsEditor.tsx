/**
 * Qwf Fields Editor - 节点级的 `.qwf` 字段编辑
 *
 * 画布的 Config 表单覆盖的是"常用的几个字段"，但 `.qwf` 里一条语句能带的东西
 * 更多：timeout / retries / retryDelay / onError / note / when / effect / export，
 * 以及各 op 的扩展字段（args / reportDataset / datasetId / kind / operator …）。
 *
 * 这些字段的键名必须和后端 `dsl/canvas_ir.py` 的字段表一致，否则画布 → IR
 * 这一步会丢字段（导出就不再等价）：
 *
 *   - `_OP_CONFIG_FIELDS`   IR 键 ← config 键
 *   - `_COMMON_CONFIG_FIELDS` = note, when, effect, onError, retryDelay,
 *                              export, timeout, retries
 *
 * 本组件按同一张表渲染输入框；改完写回 `node.data.config`，导出时由后端
 * `canvas_to_ir` 读走。
 */

"use client";

import { useMemo, useState } from 'react';
import { NodeType, WorkflowNode } from '../../store/workflowStore';

/** 画布节点类型 → `.qwf` 的 op（与后端 `CANVAS_TO_OP` 对齐） */
const NODE_TYPE_TO_OP: Partial<Record<NodeType, string>> = {
  experiment: 'measure',
  contrast: 'read',
  analyze: 'analyze',
  decision: 'decide',
  llm: 'ask',
  image_classification: 'classify',
  image_analysis: 'vlm',
  quality_gate: 'assert',
  print: 'say',
  notify: 'say',
  adjust_params: 'apply',
};

/** 各 op 的扩展字段（config 键 + 展示名 + 类型）—— 与 `_OP_CONFIG_FIELDS` 对齐 */
type FieldKind = 'text' | 'number' | 'json' | 'bool' | 'select';

const OP_EXTRA_FIELDS: Record<string, Array<[string, string, FieldKind, string[]]>> = {
  measure: [
    ['args', 'args（实验参数）', 'json', []],
    ['reportDataset', 'reportDataset（结果数据集名）', 'text', []],
  ],
  read: [
    ['kind', 'kind（读数种类）', 'text', []],
    ['datasetId', 'datasetId（数据集）', 'text', []],
    ['threshold', 'threshold（阈值）', 'number', []],
    ['qubit', 'qubit', 'text', []],
  ],
  analyze: [
    ['datasetId', 'datasetId（数据集）', 'text', []],
    ['command', 'command（分析命令）', 'text', []],
  ],
  assert: [
    ['operator', 'operator（比较符）', 'text', ['>', '<', '>=', '<=', '==', '!=']],
    ['value', 'value（期望值）', 'text', []],
    ['tolerance', 'tolerance（容差）', 'number', []],
    ['failWorkflow', 'failWorkflow（失败是否中断）', 'bool', []],
  ],
  say: [
    ['channel', 'channel（通道）', 'text', []],
  ],
  apply: [
    ['params', 'params（要写的参数）', 'json', []],
  ],
  decide: [
    ['mode', 'mode', 'text', []],
    ['intentPrompt', 'intentPrompt', 'text', []],
    ['systemPrompt', 'systemPrompt', 'text', []],
    ['temperature', 'temperature', 'number', []],
    ['maxTokens', 'maxTokens', 'number', []],
  ],
  ask: [
    ['messages', 'messages（对话）', 'json', []],
    ['model', 'model', 'text', []],
    ['temperature', 'temperature', 'number', []],
    ['prompt', 'prompt', 'text', []],
  ],
  classify: [
    ['imagePath', 'imagePath', 'text', []],
    ['reviewThreshold', 'reviewThreshold', 'number', []],
    ['labels', 'labels（标签集）', 'json', []],
  ],
  vlm: [
    ['imagePath', 'imagePath', 'text', []],
    ['experimentFamily', 'experimentFamily', 'text', []],
  ],
};

/** 公共字段（IR 键 = config 键），顺序即展示顺序 */
const COMMON_FIELDS: Array<[string, string, FieldKind, string[]]> = [
  ['timeout', 'timeout（超时，秒）', 'number', []],
  ['retries', 'retries（重试次数）', 'number', []],
  ['retryDelay', 'retryDelay（重试间隔，秒）', 'number', []],
  ['onError', 'onError（出错行为）', 'select', ['halt', 'continue', 'retry']],
  ['note', 'note（备注）', 'text', []],
  ['when', 'when（执行守卫）', 'text', []],
  ['effect', 'effect（副作用标记）', 'text', []],
  ['export', 'export（导出字段）', 'text', []],
];

/** 这些 config 键由上面的表单负责，这里不重复列 */
const ALREADY_EDITED: Record<string, string[]> = {
  measure: ['fn', 'params', 'timeout', 'retries'],
  read: [],
  analyze: [],
  assert: ['metric', 'threshold'],
  say: ['message', 'template'],
  apply: [],
  decide: [],
  ask: [],
  classify: [],
  vlm: [],
};

interface Props {
  node: WorkflowNode;
  config: Record<string, unknown>;
  onChange: (key: string, value: unknown) => void;
}

const isPlainObj = (v: unknown): v is Record<string, unknown> =>
  typeof v === 'object' && v !== null && !Array.isArray(v);

export default function QwfFieldsEditor({ node, config, onChange }: Props) {
  const [jsonDrafts, setJsonDrafts] = useState<Record<string, string>>({});
  const [jsonErrors, setJsonErrors] = useState<Record<string, string>>({});

  const op = NODE_TYPE_TO_OP[node.data.type];

  /** 画布没字段表覆盖、但原语句里有的字段 —— 后端会自动保留，展示出来让人放心 */
  const preserved = useMemo(() => {
    const stmt = (node.data.dsl?.stmt || {}) as Record<string, unknown>;
    const common = new Set(COMMON_FIELDS.map(([k]) => k));
    const mapped = new Set(
      (op ? (OP_EXTRA_FIELDS[op] || []).map(([k]) => k) : []).concat([
        'op', 'as', 'source',
      ])
    );
    return Object.keys(stmt).filter(
      (k) => !common.has(k) && !mapped.has(k) &&
        !['fn', 'qubit', 'params', 'message', 'template', 'metric', 'threshold',
          'value', 'operator', 'tolerance', 'failWorkflow', 'onFail',
          'datasetId', 'command', 'kind', 'reportDataset', 'args',
          'channel', 'mode', 'intentPrompt', 'systemPrompt', 'temperature',
          'maxTokens', 'messages', 'model', 'prompt', 'imagePath',
          'reviewThreshold', 'labels', 'experimentFamily'].includes(k)
    );
  }, [node.data.dsl?.stmt, op]);

  if (!op) {
    return (
      <div style={{ fontSize: '11px', color: '#64748b', lineHeight: 1.6 }}>
        这个节点类型（{node.data.type}）没有对应的 `.qwf` op，
        它的原始语句会原样保留在导出结果里。
      </div>
    );
  }

  const extras = (OP_EXTRA_FIELDS[op] || []).filter(
    ([key]) => !(ALREADY_EDITED[op] || []).includes(key)
  );

  const renderInput = (
    key: string,
    label: string,
    kind: FieldKind,
    options: string[]
  ) => {
    const value = config[key];
    const rowStyle: React.CSSProperties = {
      marginBottom: '10px',
      paddingBottom: '8px',
      borderBottom: '1px solid #1e293b',
    };

    if (kind === 'json') {
      const draft = jsonDrafts[key] !== undefined
        ? jsonDrafts[key]
        : value === undefined || value === null
          ? ''
          : JSON.stringify(value, null, 2);
      return (
        <div key={key} style={rowStyle}>
          <label style={{ fontSize: '11px', color: '#94a3b8', display: 'block', marginBottom: '4px' }}>
            {label}
          </label>
          <textarea
            value={draft}
            onChange={(e) => {
              setJsonDrafts((d) => ({ ...d, [key]: e.target.value }));
              setJsonErrors((e) => ({ ...e, [key]: '' }));
            }}
            onBlur={() => {
              const raw = (jsonDrafts[key] ?? draft).trim();
              if (!raw) {
                onChange(key, undefined);
                return;
              }
              try {
                const parsed = JSON.parse(raw);
                onChange(key, parsed);
                setJsonErrors((e) => ({ ...e, [key]: '' }));
              } catch (err: any) {
                setJsonErrors((e) => ({ ...e, [key]: err?.message || 'JSON 解析失败' }));
              }
            }}
            rows={4}
            style={{
              width: '100%',
              boxSizing: 'border-box',
              background: '#0f172a',
              color: '#e2e8f0',
              border: `1px solid ${jsonErrors[key] ? '#7f1d1d' : '#334155'}`,
              borderRadius: '4px',
              padding: '6px',
              fontFamily: 'monospace',
              fontSize: '10px',
              resize: 'vertical',
            }}
          />
          {jsonErrors[key] && (
            <div style={{ fontSize: '10px', color: '#f87171', marginTop: '2px' }}>
              {jsonErrors[key]}
            </div>
          )}
        </div>
      );
    }

    if (kind === 'bool') {
      return (
        <div key={key} style={rowStyle}>
          <label style={{ fontSize: '11px', color: '#94a3b8', display: 'flex', alignItems: 'center', gap: '6px' }}>
            <input
              type="checkbox"
              checked={value === undefined ? true : Boolean(value)}
              onChange={(e) => onChange(key, e.target.checked)}
            />
            {label}
          </label>
        </div>
      );
    }

    if (kind === 'select') {
      return (
        <div key={key} style={rowStyle}>
          <label style={{ fontSize: '11px', color: '#94a3b8', display: 'block', marginBottom: '4px' }}>
            {label}
          </label>
          <select
            value={value === undefined || value === null ? '' : String(value)}
            onChange={(e) => onChange(key, e.target.value || undefined)}
            style={{
              width: '100%',
              background: '#0f172a',
              color: '#e2e8f0',
              border: '1px solid #334155',
              borderRadius: '4px',
              padding: '5px',
              fontSize: '11px',
            }}
          >
            <option value="">（未设置）</option>
            {options.map((o) => (
              <option key={o} value={o}>{o}</option>
            ))}
          </select>
        </div>
      );
    }

    if (kind === 'number') {
      return (
        <div key={key} style={rowStyle}>
          <label style={{ fontSize: '11px', color: '#94a3b8', display: 'block', marginBottom: '4px' }}>
            {label}
          </label>
          <input
            type="number"
            value={value === undefined || value === null ? '' : String(value)}
            onChange={(e) => {
              const raw = e.target.value;
              onChange(key, raw === '' ? undefined : Number(raw));
            }}
            style={{
              width: '100%',
              boxSizing: 'border-box',
              background: '#0f172a',
              color: '#e2e8f0',
              border: '1px solid #334155',
              borderRadius: '4px',
              padding: '5px',
              fontSize: '11px',
            }}
          />
        </div>
      );
    }

    return (
      <div key={key} style={rowStyle}>
        <label style={{ fontSize: '11px', color: '#94a3b8', display: 'block', marginBottom: '4px' }}>
          {label}
        </label>
        <input
          type="text"
          value={value === undefined || value === null ? '' : String(value)}
          onChange={(e) => onChange(key, e.target.value || undefined)}
          style={{
            width: '100%',
            boxSizing: 'border-box',
            background: '#0f172a',
            color: '#e2e8f0',
            border: '1px solid #334155',
            borderRadius: '4px',
            padding: '5px',
            fontSize: '11px',
          }}
        />
      </div>
    );
  };

  return (
    <div style={{ marginTop: '14px', paddingTop: '10px', borderTop: '1px solid #334155' }}>
      <div style={{ fontSize: '12px', fontWeight: 700, color: '#38bdf8', marginBottom: '2px' }}>
        📄 .qwf 语句字段
      </div>
      <div style={{ fontSize: '10px', color: '#64748b', marginBottom: '10px', lineHeight: 1.5 }}>
        这些字段会原样写进导出的 `.qwf`（op: <code style={{ color: '#22d3ee' }}>{op}</code>）。
        改完可以到 <code style={{ color: '#22d3ee' }}>DSL</code> 标签页看生成结果。
      </div>

      {extras.length > 0 && (
        <>
          <div style={{ fontSize: '10px', color: '#64748b', marginBottom: '6px' }}>op 扩展字段</div>
          {extras.map(([key, label, kind, options]) => renderInput(key, label, kind, options))}
        </>
      )}

      <div style={{ fontSize: '10px', color: '#64748b', marginBottom: '6px' }}>语句公共字段</div>
      {COMMON_FIELDS.map(([key, label, kind, options]) => renderInput(key, label, kind, options))}

      {preserved.length > 0 && (
        <div style={{ marginTop: '8px', fontSize: '10px', color: '#64748b', lineHeight: 1.6 }}>
          画布没有对应控件、但会原样保留的 `.qwf` 字段：
          <span style={{ color: '#a78bfa' }}>{preserved.join(', ')}</span>
        </div>
      )}
    </div>
  );
}
