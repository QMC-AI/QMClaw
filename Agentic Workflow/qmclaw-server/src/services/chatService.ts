/**
 * Chat Service - Session management for model chat testing
 *
 * Stores chat sessions in data/chat_history/ directory
 */

import * as fs from 'fs';
import * as path from 'path';

const CHAT_DIR = path.join(process.cwd(), 'data', 'chat_history');

// ── Types ────────────────────────────────────────────────────────────

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

// ── Directory Setup ──────────────────────────────────────────────────

function ensureChatDir(): void {
  if (!fs.existsSync(CHAT_DIR)) {
    fs.mkdirSync(CHAT_DIR, { recursive: true });
  }
}

// ── CRUD Operations ──────────────────────────────────────────────────

export function createSession(modelId: string, modelName: string): ChatSession {
  ensureChatDir();
  const now = new Date().toISOString();
  const session: ChatSession = {
    id: `chat_${Date.now()}`,
    modelId,
    modelName,
    messages: [],
    createdAt: now,
    updatedAt: now,
  };
  const filePath = path.join(CHAT_DIR, `${session.id}.json`);
  fs.writeFileSync(filePath, JSON.stringify(session, null, 2), 'utf-8');
  console.log(`[chat-service] Created session: ${session.id} for model: ${modelName}`);
  return session;
}

export function addMessage(
  sessionId: string,
  message: Omit<ChatMessage, 'timestamp'>
): ChatSession | null {
  ensureChatDir();
  const filePath = path.join(CHAT_DIR, `${sessionId}.json`);
  if (!fs.existsSync(filePath)) return null;

  try {
    const content = fs.readFileSync(filePath, 'utf-8');
    const session: ChatSession = JSON.parse(content);
    session.messages.push({ ...message, timestamp: new Date().toISOString() });
    session.updatedAt = new Date().toISOString();
    fs.writeFileSync(filePath, JSON.stringify(session, null, 2), 'utf-8');
    return session;
  } catch {
    return null;
  }
}

export function getSession(sessionId: string): ChatSession | null {
  ensureChatDir();
  const filePath = path.join(CHAT_DIR, `${sessionId}.json`);
  if (!fs.existsSync(filePath)) return null;
  try {
    const content = fs.readFileSync(filePath, 'utf-8');
    return JSON.parse(content) as ChatSession;
  } catch {
    return null;
  }
}

export function listSessions(): ChatSession[] {
  ensureChatDir();
  const files = fs.readdirSync(CHAT_DIR).filter((f) => f.endsWith('.json'));
  const sessions: ChatSession[] = [];
  for (const file of files) {
    try {
      const content = fs.readFileSync(path.join(CHAT_DIR, file), 'utf-8');
      sessions.push(JSON.parse(content) as ChatSession);
    } catch (e) {
      console.error(`[chat-service] Failed to read ${file}:`, e);
    }
  }
  return sessions.sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime());
}

export function deleteSession(sessionId: string): boolean {
  ensureChatDir();
  const filePath = path.join(CHAT_DIR, `${sessionId}.json`);
  if (!fs.existsSync(filePath)) return false;
  fs.unlinkSync(filePath);
  console.log(`[chat-service] Deleted session: ${sessionId}`);
  return true;
}

export function getSessionsByModel(modelId: string): ChatSession[] {
  return listSessions().filter((s) => s.modelId === modelId);
}

export function updateSession(
  sessionId: string,
  updates: Partial<Pick<ChatSession, 'messages'>>
): ChatSession | null {
  ensureChatDir();
  const filePath = path.join(CHAT_DIR, `${sessionId}.json`);
  if (!fs.existsSync(filePath)) return null;

  try {
    const content = fs.readFileSync(filePath, 'utf-8');
    const session: ChatSession = JSON.parse(content);
    if (updates.messages) {
      session.messages = updates.messages;
    }
    session.updatedAt = new Date().toISOString();
    fs.writeFileSync(filePath, JSON.stringify(session, null, 2), 'utf-8');
    return session;
  } catch {
    return null;
  }
}
