/**
 * Model Registry Service - CRUD operations for LLM models
 *
 * =============================================================================
 * 模型配置存储
 * =============================================================================
 *
 * 所有模型配置保存在 `config/model_configs.json` 文件中。
 *
 * API Key 说明：
 * - API Key 从环境变量读取，不保存在配置文件中
 * - 需要在 .env 或系统环境变量中设置：
 *   - OPENAI_API_KEY      (OpenAI)
 *   - MINIMAX_API_KEY     (MiniMax)
 *   - ANTHROPIC_API_KEY   (Anthropic/Claude)
 *   - DEEPSEEK_API_KEY    (DeepSeek)
 *
 * 配置文件格式：
 * {
 *   "models": [
 *     {
 *       "id": "model_1234567890",
 *       "name": "GPT-4o",
 *       "provider": "openai",           // openai | minimax | anthropic | deepseek
 *       "modelId": "gpt-4o",            // API 模型 ID
 *       "baseUrl": "https://...",       // 可选：自定义 API 端点
 *       "enabled": true,                // 是否启用
 *       "capabilities": ["text", "vision"],  // text | vision | function_calling
 *       "config": {
 *         "temperature": 0.3,           // 温度参数
 *         "maxTokens": 500,             // 最大 token 数
 *         "systemPrompt": "..."         // 可选：默认系统提示词
 *       },
 *       "createdAt": "2024-...",
 *       "updatedAt": "2024-..."
 *     },
 *     ...
 *   ]
 * }
 *
 * =============================================================================
 */

import * as fs from 'fs';
import * as path from 'path';

// 配置文件路径
const CONFIG_DIR = path.join(process.cwd(), 'config');
const MODELS_CONFIG_FILE = path.join(CONFIG_DIR, 'model_configs.json');

// ── Types ────────────────────────────────────────────────────────────

export type Provider = 'openai' | 'minimax' | 'anthropic' | 'deepseek';
export type Capability = 'text' | 'vision' | 'function_calling';

export interface LLMModel {
  id: string;
  name: string;
  provider: Provider;
  modelId: string;
  baseUrl?: string;
  enabled: boolean;
  capabilities: Capability[];
  config: {
    temperature?: number;
    maxTokens?: number;
    systemPrompt?: string;
  };
  createdAt: string;
  updatedAt: string;
}

interface ModelsConfig {
  models: LLMModel[];
}

// ── Default Models ───────────────────────────────────────────────────

/**
 * 默认模型列表（首次初始化时使用）
 * 系统启动时如果配置文件不存在，会自动创建这些默认模型
 */
export const DEFAULT_MODELS: Omit<LLMModel, 'id' | 'createdAt' | 'updatedAt'>[] = [
  {
    name: 'GPT-4o',
    provider: 'openai',
    modelId: 'gpt-4o',
    enabled: true,
    capabilities: ['text', 'vision'],
    config: { temperature: 0.3, maxTokens: 500 },
  },
  {
    name: 'GPT-4o Mini',
    provider: 'openai',
    modelId: 'gpt-4o-mini',
    enabled: true,
    capabilities: ['text', 'vision'],
    config: { temperature: 0.3, maxTokens: 500 },
  },
  {
    name: 'GPT-4 Turbo',
    provider: 'openai',
    modelId: 'gpt-4-turbo',
    enabled: true,
    capabilities: ['text', 'vision'],
    config: { temperature: 0.3, maxTokens: 500 },
  },
  {
    name: 'Claude 3.5 Sonnet',
    provider: 'anthropic',
    modelId: 'claude-3-5-sonnet-20241022',
    enabled: true,
    capabilities: ['text', 'vision'],
    config: { temperature: 0.3, maxTokens: 500 },
  },
  {
    name: 'DeepSeek Chat',
    provider: 'deepseek',
    modelId: 'deepseek-chat',
    enabled: true,
    capabilities: ['text'],
    config: { temperature: 0.3, maxTokens: 500 },
  },
  {
    name: 'MiniMax Text-01',
    provider: 'minimax',
    modelId: 'MiniMax-Text-01',
    enabled: true,
    capabilities: ['text'],
    config: { temperature: 0.3, maxTokens: 500 },
  },
  {
    name: 'MiniMax ABAB6.5S',
    provider: 'minimax',
    modelId: 'abab6.5s-chat',
    enabled: true,
    capabilities: ['text'],
    config: { temperature: 0.3, maxTokens: 500 },
  },
  {
    name: 'MiniMax ABAB6',
    provider: 'minimax',
    modelId: 'abab6-chat',
    enabled: true,
    capabilities: ['text'],
    config: { temperature: 0.3, maxTokens: 500 },
  },
  {
    name: 'MiniMax M2.7',
    provider: 'minimax',
    modelId: 'minimax-m2.7',
    enabled: true,
    capabilities: ['text'],
    config: { temperature: 0.3, maxTokens: 500 },
  },
];

// ── File Operations ──────────────────────────────────────────────────

/**
 * 确保配置目录存在
 */
function ensureConfigDir(): void {
  if (!fs.existsSync(CONFIG_DIR)) {
    fs.mkdirSync(CONFIG_DIR, { recursive: true });
  }
}

/**
 * 读取配置文件
 * @returns ModelsConfig 或 null（如果文件不存在）
 */
function readConfig(): ModelsConfig | null {
  ensureConfigDir();
  if (!fs.existsSync(MODELS_CONFIG_FILE)) {
    return null;
  }
  try {
    const content = fs.readFileSync(MODELS_CONFIG_FILE, 'utf-8');
    return JSON.parse(content) as ModelsConfig;
  } catch (e) {
    console.error(`[model-service] Failed to read config: ${e}`);
    return null;
  }
}

/**
 * 写入配置文件
 */
function writeConfig(config: ModelsConfig): void {
  ensureConfigDir();
  fs.writeFileSync(MODELS_CONFIG_FILE, JSON.stringify(config, null, 2), 'utf-8');
}

// ── CRUD Operations ───────────────────────────────────────────────────

/**
 * 列出所有模型
 * @returns LLMModel[] 按更新时间倒序排列
 */
export function listModels(): LLMModel[] {
  const config = readConfig();
  if (!config) return [];
  return [...config.models].sort(
    (a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime()
  );
}

/**
 * 根据 ID 获取模型
 * @param id 模型 ID
 * @returns LLMModel 或 null
 */
export function getModel(id: string): LLMModel | null {
  const config = readConfig();
  if (!config) return null;
  return config.models.find((m) => m.id === id) || null;
}

/**
 * 根据名称获取模型
 * @param name 模型名称
 * @returns LLMModel 或 null
 */
export function getModelByName(name: string): LLMModel | null {
  const models = listModels();
  return models.find((m) => m.name === name) || null;
}

/**
 * 保存模型（创建或更新）
 * @param id 模型 ID（null 表示创建新模型）
 * @param data 模型数据（不含 id、createdAt、updatedAt）
 * @returns 保存后的完整模型
 */
export function saveModel(
  id: string | null,
  data: Omit<LLMModel, 'id' | 'createdAt' | 'updatedAt'>
): LLMModel {
  const config = readConfig() || { models: [] };
  const now = new Date().toISOString();
  const modelId = id || `model_${Date.now()}`;

  // 查找是否已存在
  const existingIndex = config.models.findIndex((m) => m.id === modelId);
  const existing = existingIndex >= 0 ? config.models[existingIndex] : null;

  const model: LLMModel = {
    ...data,
    id: modelId,
    createdAt: existing?.createdAt || now,
    updatedAt: now,
  };

  if (existingIndex >= 0) {
    // 更新现有模型
    config.models[existingIndex] = model;
  } else {
    // 添加新模型
    config.models.push(model);
  }

  writeConfig(config);
  console.log(`[model-service] Saved model: ${modelId} (${model.name})`);
  return model;
}

/**
 * 删除模型
 * @param id 模型 ID
 * @returns 是否删除成功
 */
export function deleteModel(id: string): boolean {
  const config = readConfig();
  if (!config) return false;

  const index = config.models.findIndex((m) => m.id === id);
  if (index < 0) return false;

  config.models.splice(index, 1);
  writeConfig(config);
  console.log(`[model-service] Deleted model: ${id}`);
  return true;
}

// ── Initialization ───────────────────────────────────────────────────

/**
 * 初始化默认模型
 * 如果配置文件不存在，自动创建默认模型
 * 如果已存在，添加缺失的默认模型
 */
export function initializeDefaultModels(): void {
  const config = readConfig();

  if (!config) {
    // 配置文件不存在，创建默认模型
    console.log('[model-service] Initializing default models...');
    const now = new Date().toISOString();
    const models: LLMModel[] = DEFAULT_MODELS.map((m, i) => ({
      ...m,
      id: `model_${Date.now()}_${i}`,
      createdAt: now,
      updatedAt: now,
    }));
    writeConfig({ models });
    console.log(`[model-service] Created ${DEFAULT_MODELS.length} default models`);
    return;
  }

  // 配置文件已存在，检查是否需要添加缺失的默认模型
  const existingNames = new Set(config.models.map((m) => m.name));
  let added = 0;

  for (const modelData of DEFAULT_MODELS) {
    if (!existingNames.has(modelData.name)) {
      const now = new Date().toISOString();
      const model: LLMModel = {
        ...modelData,
        id: `model_${Date.now()}_${added}`,
        createdAt: now,
        updatedAt: now,
      };
      config.models.push(model);
      added++;
    }
  }

  if (added > 0) {
    writeConfig(config);
    console.log(`[model-service] Added ${added} new default model(s)`);
  }
  console.log(`[model-service] Loaded ${config.models.length} model(s)`);
}
