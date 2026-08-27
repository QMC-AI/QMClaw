/**
 * Workflow Service - 工作流持久化服务
 *
 * 管理工作流的保存、加载、列表等操作
 */

import * as fs from 'fs';
import * as path from 'path';

const DATA_DIR = path.join(process.cwd(), 'data', 'workflows');

// Ensure data directory exists
function ensureDataDir(): void {
  if (!fs.existsSync(DATA_DIR)) {
    fs.mkdirSync(DATA_DIR, { recursive: true });
  }
}

// ── Types ──────────────────────────────────────────────────────────────────────

export interface StoredWorkflow {
  id: string;
  name: string;
  version: number;
  createdAt: string;
  updatedAt: string;
  nodes: StoredNode[];
  edges: StoredEdge[];
  settings?: Record<string, unknown>;
}

export interface StoredNode {
  id: string;
  type: string;
  position: { x: number; y: number };
  config: Record<string, unknown>;
  templateId?: string;
}

export interface StoredEdge {
  id: string;
  source: string;
  target: string;
  sourceHandle?: string;
  type: 'dependency' | 'condition-pass' | 'condition-fail';
}

// ── CRUD Operations ─────────────────────────────────────────────────────────

/**
 * List all saved workflows
 */
export function listWorkflows(): StoredWorkflow[] {
  ensureDataDir();

  const files = fs.readdirSync(DATA_DIR).filter((f: string) => f.endsWith('.json'));
  const workflows: StoredWorkflow[] = [];

  for (const file of files) {
    try {
      const filePath = path.join(DATA_DIR, file);
      const content = fs.readFileSync(filePath, 'utf-8');
      const workflow = JSON.parse(content) as StoredWorkflow;
      workflows.push(workflow);
    } catch (e) {
      console.error(`[workflow-service] Failed to read ${file}:`, e);
    }
  }

  // Sort by updatedAt descending
  workflows.sort((a, b) =>
    new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime()
  );

  return workflows;
}

/**
 * Get a workflow by ID
 */
export function getWorkflow(id: string): StoredWorkflow | null {
  ensureDataDir();

  const filePath = path.join(DATA_DIR, `${id}.json`);
  if (!fs.existsSync(filePath)) {
    return null;
  }

  try {
    const content = fs.readFileSync(filePath, 'utf-8');
    return JSON.parse(content) as StoredWorkflow;
  } catch (e) {
    console.error(`[workflow-service] Failed to read workflow ${id}:`, e);
    return null;
  }
}

/**
 * Save a new workflow or update an existing one
 */
export function saveWorkflow(
  id: string | null,
  data: {
    name: string;
    nodes: StoredNode[];
    edges: StoredEdge[];
    settings?: Record<string, unknown>;
  }
): StoredWorkflow {
  ensureDataDir();

  const now = new Date().toISOString();
  const workflowId = id || `wf_${Date.now()}`;

  // Check if exists for versioning
  const existing = id ? getWorkflow(id) : null;

  const workflow: StoredWorkflow = {
    id: workflowId,
    name: data.name,
    version: existing ? existing.version + 1 : 1,
    createdAt: existing ? existing.createdAt : now,
    updatedAt: now,
    nodes: data.nodes,
    edges: data.edges,
    settings: data.settings,
  };

  const filePath = path.join(DATA_DIR, `${workflowId}.json`);
  fs.writeFileSync(filePath, JSON.stringify(workflow, null, 2), 'utf-8');

  console.log(`[workflow-service] Saved workflow: ${workflowId}`);

  return workflow;
}

/**
 * Delete a workflow
 */
export function deleteWorkflow(id: string): boolean {
  ensureDataDir();

  const filePath = path.join(DATA_DIR, `${id}.json`);
  if (!fs.existsSync(filePath)) {
    return false;
  }

  fs.unlinkSync(filePath);
  console.log(`[workflow-service] Deleted workflow: ${id}`);
  return true;
}

/**
 * Export workflow as JSON string
 */
export function exportWorkflow(id: string): string | null {
  const workflow = getWorkflow(id);
  if (!workflow) return null;
  return JSON.stringify(workflow, null, 2);
}

/**
 * Import workflow from JSON string
 */
export function importWorkflow(jsonStr: string): StoredWorkflow | null {
  try {
    const data = JSON.parse(jsonStr) as StoredWorkflow;

    // Validate structure
    if (!data.name || !Array.isArray(data.nodes) || !Array.isArray(data.edges)) {
      console.error('[workflow-service] Invalid workflow structure');
      return null;
    }

    // Generate new ID to avoid conflicts
    const imported = saveWorkflow(null, {
      name: data.name + ' (imported)',
      nodes: data.nodes,
      edges: data.edges,
      settings: data.settings,
    });

    return imported;
  } catch (e) {
    console.error('[workflow-service] Failed to import workflow:', e);
    return null;
  }
}

// ── Template Operations ──────────────────────────────────────────────────────

const TEMPLATES_DIR = path.join(process.cwd(), 'data', 'templates');

function ensureTemplatesDir(): void {
  if (!fs.existsSync(TEMPLATES_DIR)) {
    fs.mkdirSync(TEMPLATES_DIR, { recursive: true });
  }
}

export interface NodeTemplate {
  id: string;
  name: string;
  type: string;
  config: Record<string, unknown>;
  tags: string[];
  author: string;
  version: string;
  createdAt: string;
  updatedAt: string;
}

/**
 * List all node templates
 */
export function listTemplates(): NodeTemplate[] {
  ensureTemplatesDir();

  const files = fs.readdirSync(TEMPLATES_DIR).filter((f: string) => f.endsWith('.json'));
  const templates: NodeTemplate[] = [];

  for (const file of files) {
    try {
      const filePath = path.join(TEMPLATES_DIR, file);
      const content = fs.readFileSync(filePath, 'utf-8');
      const template = JSON.parse(content) as NodeTemplate;
      templates.push(template);
    } catch (e) {
      console.error(`[workflow-service] Failed to read template ${file}:`, e);
    }
  }

  return templates;
}

/**
 * Save a node template
 */
export function saveTemplate(
  data: Omit<NodeTemplate, 'id' | 'createdAt' | 'updatedAt'>
): NodeTemplate {
  ensureTemplatesDir();

  const now = new Date().toISOString();
  const templateId = `tmpl_${Date.now()}`;

  const template: NodeTemplate = {
    ...data,
    id: templateId,
    createdAt: now,
    updatedAt: now,
  };

  const filePath = path.join(TEMPLATES_DIR, `${templateId}.json`);
  fs.writeFileSync(filePath, JSON.stringify(template, null, 2), 'utf-8');

  console.log(`[workflow-service] Saved template: ${templateId}`);

  return template;
}

/**
 * Delete a template
 */
export function deleteTemplate(id: string): boolean {
  ensureTemplatesDir();

  const filePath = path.join(TEMPLATES_DIR, `${id}.json`);
  if (!fs.existsSync(filePath)) {
    return false;
  }

  fs.unlinkSync(filePath);
  console.log(`[workflow-service] Deleted template: ${id}`);
  return true;
}
