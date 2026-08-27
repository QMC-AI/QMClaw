/**
 * Model types for LLM model registry
 */

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

export interface ChatMessage {
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: string;
}

export interface ChatSession {
  id: string;
  modelId: string;
  modelName: string;
  messages: ChatMessage[];
  createdAt: string;
  updatedAt: string;
}

export interface ChatTestResult {
  content: string;
  model: string;
  modelId: string;
  usage?: {
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
  };
}
