"use client";

/**
 * HermesChatPanel - Chat component without Sessions sidebar
 *
 * The Sessions sidebar is handled by HermesPage at the outer layout level.
 * This component only handles the chat functionality.
 */

import { useState, useEffect, useRef, useCallback } from "react";
import { api } from "../lib/api";
import MarkdownContent from "../lib/markdown";
import ReasoningBlock from "../lib/reasoning-block";
import { ToolCallsList } from "../lib/tool-call-card";

// 从消息内容中提取图片 URL
const extractImagesFromContent = (content: string): HermesMessage["images"] => {
  if (!content) return undefined;
  const images: HermesMessage["images"] = [];
  const seenUrls = new Set<string>();

  const addImage = (url: string, alt: string = 'image.png') => {
    // 去重
    if (seenUrls.has(url)) return;
    seenUrls.add(url);
    // 支持 base64, /plots/, http://, https:// 路径
    if (url.startsWith('data:image/') ||
        url.startsWith('/plots/') ||
        url.startsWith('http://') ||
        url.startsWith('https://')) {
      images.push({ data: url, name: alt || 'image.png' });
    }
  };

  // 1. 匹配 Markdown 图片语法: ![alt](url)
  const mdRegex = /!\[([^\]]*)\]\(([^)]+)\)/g;
  let match;
  while ((match = mdRegex.exec(content)) !== null) {
    const [full, alt, url] = match;
    addImage(url, alt);
  }

  // 2. 匹配纯文本中的 /plots/ URL（如 "请看这张图：/plots/temp_xxx.png"）
  // 也支持 /pplots/（Hermes 可能误加的 p）
  const plotsRegex = /(\/?p?plots\/[\w.-]+\.(?:png|jpg|jpeg|gif|webp|svg))/gi;
  while ((match = plotsRegex.exec(content)) !== null) {
    // 清理 URL：
    let url = match[1].replace(/^pplots/, '/plots').replace(/^\/?pplots/, '/plots');
    addImage(url, 'plot.png');
  }

  // 3. 匹配任何 http/https URL（带扩展名）
  const httpRegex = /(https?:\/\/[^\s)]+\.(?:png|jpg|jpeg|gif|webp|svg))/gi;
  while ((match = httpRegex.exec(content)) !== null) {
    addImage(match[1], 'image.png');
  }

  return images.length > 0 ? images : undefined;
};

// 从内容中移除图片语法（Markdown 或纯文本 URL）
const removeImagesFromContent = (content: string): string => {
  // 移除 Markdown 图片
  let result = content.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, '');
  // 移除纯文本中的 /plots/ URL（单独一行的，包含可能的 /pplots/）
  result = result.replace(/^\/?p?plots\/[\w-]+\.png\s*$/gm, '');
  // 移除重复的 URL（包含可能的 /pplots/）
  result = result.replace(/(\/?p?plots\/[\w-]+\.png)(\s*\1)+/g, '$1');
  return result.trim();
};

// 规范化 tool_calls 格式：数据库存储的是 OpenAI 格式 {id, type, function: {name, arguments}}
// 前端期望格式 {name, arguments, status, result}
const normalizeToolCalls = (toolCalls: any[] | undefined): HermesMessage["tool_calls"] | undefined => {
  if (!toolCalls || !Array.isArray(toolCalls)) return undefined;

  return toolCalls.map(tc => {
    // 如果已经是前端格式（name 字段存在），直接返回
    if (typeof tc.name === 'string') {
      return {
        ...tc,
        status: tc.status || "done",  // 历史记录默认已完成
      };
    }
    // OpenAI 格式：{id, type, function: {name, arguments}}
    if (tc.function && typeof tc.function === 'object') {
      let args = tc.function.arguments;
      // arguments 可能是字符串，需要解析
      if (typeof args === 'string') {
        try {
          args = JSON.parse(args);
        } catch {
          args = {};
        }
      }
      return {
        name: tc.function.name,
        arguments: args || {},
        status: "done" as const,  // 历史记录默认已完成
      };
    }
    // 未知格式
    return {
      name: String(tc.name || tc.function?.name || 'unknown'),
      arguments: {},
      status: "done" as const,
    };
  });
};

interface HermesMessage {
  role: "user" | "assistant";
  content: string;
  thinking?: string;
  tool_calls?: Array<{
    name: string;
    arguments: Record<string, unknown>;
    status?: "pending" | "running" | "done" | "error";
    result?: string;
  }>;
  tool_results?: Array<{
    name: string;
    result: string;
    success: boolean;
  }>;
  images?: Array<{
    data: string;      // base64 data 或 URL
    type?: string;      // MIME 类型
    name?: string;      // 文件名
  }>;
  error?: string;
  status?: "pending" | "running" | "done" | "error";
  toolLogs?: ToolLogEntry[];
}

interface ToolLogEntry {
  type: "start" | "completed";
  name: string;
  preview?: string;
  args?: Record<string, unknown>;
  duration?: number;
  is_error?: boolean;
  result_preview?: string;
}

interface HermesModel {
  id: string;
  name: string;
  provider?: string;
}

interface ApprovalRequest {
  command: string;
  description: string;
  timeout: number;
}

const DEFAULT_HERMES_MODEL = "minimax/MiniMax-M2.7";

const HERMES_TOOLSETS = [
  { id: "web", label: "Web", desc: "Search and browse" },
  { id: "vision", label: "Vision", desc: "Image analysis" },
  { id: "terminal", label: "Terminal", desc: "Command execution" },
  { id: "computer_use", label: "Computer", desc: "Desktop control", disabled: true },
];

// ── Image Display Component ───────────────────────────────────────────────────

interface ImageDisplayProps {
  img: {
    data: string;
    type?: string;
    name?: string;
  };
}

function ImageDisplay({ img }: ImageDisplayProps) {
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState(false);

  const openInNewTab = useCallback(() => {
    if (img.data.startsWith('data:')) {
      const arr = img.data.split(',');
      const mime = arr[0].match(/:(.*?);/)?.[1] || 'image/png';
      const bstr = atob(arr[1]);
      let n = bstr.length;
      const u8arr = new Uint8Array(n);
      while (n--) u8arr[n] = bstr.charCodeAt(n);
      const blob = new Blob([u8arr], { type: mime });
      const url = URL.createObjectURL(blob);
      window.open(url, '_blank');
    } else {
      window.open(img.data, '_blank');
    }
  }, [img.data]);

  // 内联下载函数
  const handleDownload = useCallback(() => {
    try {
      let blob: Blob;
      let filename = img.name || 'image.png';

      if (img.data.startsWith('data:')) {
        const arr = img.data.split(',');
        const mime = img.type || arr[0].match(/:(.*?);/)?.[1] || 'image/png';
        const bstr = atob(arr[1]);
        let n = bstr.length;
        const u8arr = new Uint8Array(n);
        while (n--) u8arr[n] = bstr.charCodeAt(n);
        blob = new Blob([u8arr], { type: mime });
      } else if (img.data.startsWith('http') || img.data.startsWith('/')) {
        fetch(img.data)
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
        console.error('Invalid image data format');
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
      background: "#0f172a",
      borderRadius: "0.5rem",
      padding: "0.5rem",
      marginTop: "0.25rem",
      border: `1px solid ${error ? "#ef4444" : "#334155"}`,
    }}>
      <div style={{ color: "#64748b", fontSize: "0.65rem", marginBottom: "0.25rem" }}>
        {img.name || "image"}
        {error && <span style={{ color: "#ef4444", marginLeft: "0.5rem" }}>❌ 加载失败</span>}
      </div>

      {!loaded && !error && (
        <div style={{
          height: "150px",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: "#1e293b",
          borderRadius: "0.25rem",
          color: "#64748b",
          fontSize: "0.75rem",
        }}>
          加载中...
        </div>
      )}

      {error && (
        <div style={{
          padding: "0.75rem",
          background: "#1e293b",
          borderRadius: "0.25rem",
          color: "#ef4444",
          fontSize: "0.7rem",
        }}>
          <div>⚠️ 图片加载失败</div>
          <div style={{ color: "#64748b", marginTop: "0.25rem", wordBreak: "break-all" }}>
            URL: {img.data.substring(0, 100)}{img.data.length > 100 ? "..." : ""}
          </div>
        </div>
      )}

      <img
        src={img.data}
        alt={img.name || "image"}
        style={{
          maxWidth: "100%",
          maxHeight: "300px",
          borderRadius: "0.25rem",
          display: loaded ? "block" : "none",
          cursor: "zoom-in",
        }}
        onLoad={() => setLoaded(true)}
        onError={() => setError(true)}
        onClick={openInNewTab}
      />

      <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.5rem" }}>
        <button
          onClick={handleDownload}
          style={{
            padding: "0.25rem 0.5rem",
            background: "#1e3a5f",
            border: "1px solid #334155",
            borderRadius: "0.25rem",
            color: "#38bdf8",
            fontSize: "0.7rem",
            cursor: "pointer",
          }}
        >
          💾 Download
        </button>
        <button
          onClick={openInNewTab}
          style={{
            padding: "0.25rem 0.5rem",
            background: "#1e3a5f",
            border: "1px solid #334155",
            borderRadius: "0.25rem",
            color: "#38bdf8",
            fontSize: "0.7rem",
            cursor: "pointer",
          }}
        >
          🔍 Full View
        </button>
      </div>
    </div>
  );
}

// ── HermesChatPanel ───────────────────────────────────────────────────────────

export default function HermesChatPanel() {
  const [messages, setMessages] = useState<HermesMessage[]>([]);
  const [input, setInput] = useState("");
  const [model, setModel] = useState(DEFAULT_HERMES_MODEL);
  const [running, setRunning] = useState(false);
  const [sessionId, setSessionId] = useState(() => {
    const stored = localStorage.getItem('hermes_session_id');
    if (stored) return stored;
    const newId = `hermes_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    localStorage.setItem('hermes_session_id', newId);
    return newId;
  });

  const [enabledToolsets, setEnabledToolsets] = useState<string[]>(["web"]);
  const [models, setModels] = useState<HermesModel[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [inputCount, setInputCount] = useState(0); // 用于激活测试按钮

  // WebSocket state
  const [ws, setWs] = useState<WebSocket | null>(null);
  const [wsConnected, setWsConnected] = useState(false);

  // Approval dialog state
  const [pendingApproval, setPendingApproval] = useState<ApprovalRequest | null>(null);
  const [approvalCountdown, setApprovalCountdown] = useState(120);

  const bottomRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Fetch available models
  useEffect(() => {
    api.hermesGetModels()
      .then((res: any) => setModels(res.models || []))
      .catch(() => {
        setModels([
          { id: "MiniMax-M2.7", name: "MiniMax M2.7", provider: "minimax" },
          { id: "claude-3-5-sonnet-20241022", name: "Claude 3.5 Sonnet", provider: "anthropic" },
          { id: "deepseek-chat", name: "DeepSeek Chat", provider: "deepseek" },
        ]);
      });
  }, []);

  // Load initial session history on mount
  useEffect(() => {
    const loadInitialHistory = async () => {
      // sessionId 来自初始化函数，如果 localStorage 有值就已经设置好了
      const storedSessionId = localStorage.getItem('hermes_session_id');
      if (!storedSessionId) return;

      try {
        const history = await api.hermesGetSessionMessages(storedSessionId);
        if (history.messages && history.messages.length > 0) {
          console.log("Loading initial history:", history.messages.length, "messages");
          setMessages(history.messages.map((msg: any) => ({
            role: msg.role || "user",
            content: msg.content || "",
            // 后端存储的是 reasoning/reasoning_content，前端使用 thinking
            thinking: msg.thinking || msg.reasoning || msg.reasoning_content,
            // 规范化 tool_calls 格式：数据库存储的是 OpenAI 格式 {id, type, function: {name, arguments}}
            // 前端期望格式 {name, arguments, status, result}
            tool_calls: normalizeToolCalls(msg.tool_calls),
            tool_results: msg.tool_results,
            status: "done",
          })));
        }
      } catch (err) {
        console.error("Failed to load initial session history:", err);
      }
    };

    loadInitialHistory();
  }, []); // 只在首次挂载时运行

  // Listen for session change events from HermesPage
  useEffect(() => {
    const handleSessionChange = async (e: CustomEvent) => {
      const newSessionId = e.detail;
      console.log("Session changed to:", newSessionId);
      setSessionId(newSessionId);
      setMessages([]);
      setError(null);

      // Load history for the new session
      try {
        const history = await api.hermesGetSessionMessages(newSessionId);
        if (history.messages && history.messages.length > 0) {
          console.log("Loading history:", history.messages.length, "messages");
          setMessages(history.messages.map((msg: any) => ({
            role: msg.role || "user",
            content: msg.content || "",
            // 后端存储的是 reasoning/reasoning_content，前端使用 thinking
            thinking: msg.thinking || msg.reasoning || msg.reasoning_content,
            // 规范化 tool_calls 格式
            tool_calls: normalizeToolCalls(msg.tool_calls),
            tool_results: msg.tool_results,
            status: "done",
          })));
        } else {
          console.log("No history for session:", newSessionId);
        }
      } catch (err) {
        console.error("Failed to load session history:", err);
        // 不显示错误，因为新会话本来就没有历史记录
      }
    };

    window.addEventListener('hermes-session-change', handleSessionChange as unknown as EventListener);
    return () => {
      window.removeEventListener('hermes-session-change', handleSessionChange as unknown as EventListener);
    };
  }, []);

  // WebSocket connection for Approval handling
  useEffect(() => {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `ws://localhost:3013?session_id=${sessionId}`;

    const websocket = new WebSocket(wsUrl);
    let mounted = true;

    websocket.onopen = () => {
      if (mounted) {
        console.log("Hermes WebSocket connected");
        setWsConnected(true);
      }
    };

    websocket.onclose = () => {
      if (mounted) {
        console.log("Hermes WebSocket disconnected");
        setWsConnected(false);
        setWs(null);
      }
    };

    websocket.onerror = (e) => {
      if (mounted) {
        console.error("Hermes WebSocket error:", e);
        setWsConnected(false);
      }
    };

    websocket.onmessage = (event) => {
      if (!mounted) return;
      try {
        const msg = JSON.parse(event.data);

        if (msg.type === "approval_request") {
          setPendingApproval({
            command: msg.command,
            description: msg.description,
            timeout: msg.timeout || 120,
          });
          setApprovalCountdown(msg.timeout || 120);
        } else if (msg.type === "approval_timeout") {
          setPendingApproval(null);
        } else if (msg.type === "thinking") {
          console.log("[WS] Received thinking event:", msg);
          setMessages(prev => {
            const newMessages = [...prev];
            const lastIdx = newMessages.length - 1;
            if (lastIdx >= 0 && newMessages[lastIdx].role === "assistant") {
              newMessages[lastIdx] = {
                ...newMessages[lastIdx],
                thinking: msg.data?.content || msg.content,
              };
              console.log("[WS] Updated thinking:", newMessages[lastIdx].thinking);
            } else {
              console.log("[WS] No assistant message found for thinking event");
            }
            return newMessages;
          });
        } else if (msg.type === "reasoning") {
          // reasoning 事件 - 实际的推理内容
          console.log("[WS] Received reasoning event:", msg);
          setMessages(prev => {
            const newMessages = [...prev];
            const lastIdx = newMessages.length - 1;
            if (lastIdx >= 0 && newMessages[lastIdx].role === "assistant") {
              const currentThinking = newMessages[lastIdx].thinking || "";
              const newContent = msg.data?.content || msg.content || "";
              // 追加推理内容
              newMessages[lastIdx] = {
                ...newMessages[lastIdx],
                thinking: currentThinking + newContent,
              };
              console.log("[WS] Updated reasoning:", newMessages[lastIdx].thinking);
            } else {
              console.log("[WS] No assistant message found for reasoning event");
            }
            return newMessages;
          });
        } else if (msg.type === "tool_start") {
          setMessages(prev => {
            const newMessages = [...prev];
            const lastIdx = newMessages.length - 1;
            if (lastIdx >= 0 && newMessages[lastIdx].role === "assistant") {
              const toolCalls = [...(newMessages[lastIdx].tool_calls || [])];
              toolCalls.push({
                name: msg.data?.name || msg.tool || "unknown",
                arguments: msg.data?.args || msg.arguments || {},
                status: "running",
              });
              newMessages[lastIdx] = {
                ...newMessages[lastIdx],
                tool_calls: toolCalls,
              };
            }
            return newMessages;
          });
        } else if (msg.type === "tool_complete") {
          const toolName = msg.data?.name || msg.name || "";
          const toolResult = msg.data?.result || msg.result;

          // 尝试从结果中提取图片
          const extractImages = (result: any): HermesMessage["images"] => {
            const images: HermesMessage["images"] = [];

            // 如果结果不是对象，尝试解析 JSON
            let resultObj: any = result;
            if (typeof result === 'string') {
              try {
                resultObj = JSON.parse(result);
              } catch (e) {
                // 不是 JSON 字符串，直接返回空
                return undefined;
              }
            }

            if (!resultObj || typeof resultObj !== 'object') {
              return undefined;
            }

            // 检查多种可能的图片格式
            const addImage = (data: string, name: string = 'image.png') => {
              if (!data || typeof data !== 'string') return;
              let imageData = data;
              let imageType = 'image/png';
              if (data.startsWith('data:')) {
                const match = data.match(/data:([^;]+);/);
                imageType = match?.[1] || 'image/png';
              } else if (!data.startsWith('http') && !data.startsWith('/')) {
                return; // 不是有效的图片数据
              }
              images.push({ data: imageData, type: imageType, name });
            };

            // image_url 字段 (短 URL 格式，如 /plots/temp_xxx.png)
            if (resultObj.image_url) {
              addImage(resultObj.image_url, 'plot.png');
            }
            // image 字段 (Analysis Service 返回格式)
            if (resultObj.image) {
              addImage(resultObj.image, 'plot.png');
            }
            // plotPath 字段
            if (resultObj.plotPath) {
              addImage(resultObj.plotPath, 'plot.png');
            }
            // plotUrl 字段
            if (resultObj.plotUrl) {
              addImage(resultObj.plotUrl, 'plot.png');
            }
            // success + data.image (ToolResult 格式)
            if (resultObj.success && resultObj.data?.image) {
              addImage(resultObj.data.image, 'plot.png');
            }

            return images.length > 0 ? images : undefined;
          };

          setMessages(prev => {
            const newMessages = [...prev];
            const lastIdx = newMessages.length - 1;
            if (lastIdx >= 0 && newMessages[lastIdx].role === "assistant") {
              const toolCalls = newMessages[lastIdx].tool_calls?.map(tc => {
                if (tc.name === toolName) {
                  return {
                    ...tc,
                    status: "done" as const,
                    result: typeof toolResult === 'string' ? toolResult : JSON.stringify(toolResult),
                  };
                }
                return tc;
              });
              const images = extractImages(toolResult);
              newMessages[lastIdx] = {
                ...newMessages[lastIdx],
                tool_calls: toolCalls,
                ...(images ? { images } : {}),
              };
            }
            return newMessages;
          });
        } else if (msg.type === "stream") {
          setMessages(prev => {
            const newMessages = [...prev];
            const lastIdx = newMessages.length - 1;
            if (lastIdx >= 0 && newMessages[lastIdx].role === "assistant") {
              const newContent = (newMessages[lastIdx].content || "") + (msg.data?.content || msg.content || "");
              const images = extractImagesFromContent(newContent);
              newMessages[lastIdx] = {
                ...newMessages[lastIdx],
                content: removeImagesFromContent(newContent),
                status: "running",
                ...(images ? { images } : {}),
              };
            }
            return newMessages;
          });
        } else if (msg.type === "tool_log") {
          // 工具执行日志
          const logEntry: ToolLogEntry = {
            type: msg.data?.type,
            name: msg.data?.name || msg.name,
            preview: msg.data?.preview || msg.preview,
            args: msg.data?.args || msg.args,
            duration: msg.data?.duration || msg.duration,
            is_error: msg.data?.is_error || msg.is_error,
            result_preview: msg.data?.result_preview || msg.result_preview,
          };

          setMessages(prev => {
            const newMessages = [...prev];
            const lastIdx = newMessages.length - 1;
            if (lastIdx >= 0 && newMessages[lastIdx].role === "assistant") {
              const toolLogs = [...(newMessages[lastIdx].toolLogs || [])];
              toolLogs.push(logEntry);
              newMessages[lastIdx] = {
                ...newMessages[lastIdx],
                toolLogs,
              };
            }
            return newMessages;
          });
        }
      } catch (e) {
        console.error("Failed to parse WebSocket message:", e);
      }
    };

    setWs(websocket);

    return () => {
      mounted = false;
      if (websocket.readyState === WebSocket.OPEN) {
        websocket.close();
      }
    };
  }, [sessionId]);

  // 下载图片
  const downloadImage = useCallback((image: { data: string; type?: string; name?: string }) => {
    try {
      let blob: Blob;
      let filename = image.name || 'image.png';

      if (image.data.startsWith('data:')) {
        // base64 data URL
        const arr = image.data.split(',');
        const mime = image.type || arr[0].match(/:(.*?);/)?.[1] || 'image/png';
        const bstr = atob(arr[1]);
        let n = bstr.length;
        const u8arr = new Uint8Array(n);
        while (n--) {
          u8arr[n] = bstr.charCodeAt(n);
        }
        blob = new Blob([u8arr], { type: mime });
      } else if (image.data.startsWith('http') || image.data.startsWith('/')) {
        // HTTP URL - 需要先获取
        fetch(image.data)
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
        console.error('Invalid image data format');
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
  }, []);

  // Approval countdown timer
  useEffect(() => {
    if (!pendingApproval) return;

    const timer = setInterval(() => {
      setApprovalCountdown(prev => {
        if (prev <= 1) {
          clearInterval(timer);
          if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({
              type: "approval_response",
              response: "deny",
            }));
          }
          setPendingApproval(null);
          return 0;
        }
        return prev - 1;
      });
    }, 1000);

    return () => clearInterval(timer);
  }, [pendingApproval, ws]);

  // Auto-scroll to bottom
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const toggleToolset = (toolset: string) => {
    setEnabledToolsets(prev =>
      prev.includes(toolset)
        ? prev.filter(t => t !== toolset)
        : [...prev, toolset]
    );
  };

  // ===== 测试图片显示 =====
  const [showTestBtn, setShowTestBtn] = useState(false);
  // fileInputRef 已在上面声明（第134行）

  const testImageDisplay = useCallback(() => {
    // 打开文件选择器
    fileInputRef.current?.click();
  }, []);

  const handleFileSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = (ev) => {
      const dataUrl = ev.target?.result as string;
      if (dataUrl) {
        setMessages(prev => [...prev, {
          role: "assistant",
          content: `测试图片: ${file.name}`,
          images: [
            { data: dataUrl, name: file.name },
          ],
          status: "done",
        }]);
      }
    };
    reader.readAsDataURL(file);

    // 清空 input 以便重复选择同一文件
    e.target.value = '';
  }, []);
  // ===== 测试图片显示结束 =====

  const handleApprovalResponse = useCallback((response: string) => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({
        type: "approval_response",
        response,
      }));
    }
    setPendingApproval(null);
  }, [ws]);

  const handleSend = useCallback(async () => {
    if (!input.trim() || running) return;
    const userMsg = input.trim();
    setInput("");
    setError(null);

    setMessages(prev => [...prev, { role: "user", content: userMsg }]);

    // 初始化 assistant 消息（空内容，等待流式数据）
    setMessages(prev => [...prev, {
      role: "assistant",
      content: "",
      status: "running",
    }]);

    setRunning(true);
    const msgIndex = messages.length + 1;

    try {
      let accumulatedContent = "";
      let currentThinking = "";
      let finalResult: any = null;

      for await (const event of api.hermesChatStream(userMsg, model, sessionId)) {
        const { type, data } = event;

        if (type === "status") {
          setMessages(prev => {
            const newMessages = [...prev];
            if (newMessages[msgIndex]) {
              newMessages[msgIndex] = {
                ...newMessages[msgIndex],
                content: data.message || "Processing...",
                status: "running",
              };
            }
            return newMessages;
          });
        } else if (type === "thinking") {
          currentThinking = data.content || "";
          setMessages(prev => {
            const newMessages = [...prev];
            if (newMessages[msgIndex]) {
              newMessages[msgIndex] = {
                ...newMessages[msgIndex],
                thinking: currentThinking,
              };
            }
            return newMessages;
          });
        } else if (type === "response") {
          accumulatedContent += data.content || "";
          setMessages(prev => {
            const newMessages = [...prev];
            if (newMessages[msgIndex]) {
              newMessages[msgIndex] = {
                ...newMessages[msgIndex],
                content: accumulatedContent,
                status: "running",
              };
            }
            return newMessages;
          });
        } else if (type === "tool_call") {
          setMessages(prev => {
            const newMessages = [...prev];
            if (newMessages[msgIndex]) {
              const tool_calls = [...(newMessages[msgIndex].tool_calls || []), {
                name: data.tool,
                arguments: data.args || {},
              }];
              newMessages[msgIndex] = {
                ...newMessages[msgIndex],
                tool_calls,
              };
            }
            return newMessages;
          });
        } else if (type === "done") {
          finalResult = data;
          setMessages(prev => {
            const newMessages = [...prev];
            if (newMessages[msgIndex]) {
              newMessages[msgIndex] = {
                ...newMessages[msgIndex],
                content: data.final_response || data.response || accumulatedContent || "Done",
                status: "done",
                tool_results: data.tool_results,
              };
            }
            return newMessages;
          });
        } else if (type === "error") {
          setError(data.error || "Unknown error");
          setMessages(prev => {
            const newMessages = [...prev];
            if (newMessages[msgIndex]) {
              newMessages[msgIndex] = {
                ...newMessages[msgIndex],
                content: `❌ Error: ${data.error || "Unknown error"}`,
                status: "error",
                error: data.error,
              };
            }
            return newMessages;
          });
        }
      }

      if (!finalResult && !error) {
        setMessages(prev => {
          const newMessages = [...prev];
          if (newMessages[msgIndex]) {
            newMessages[msgIndex] = {
              ...newMessages[msgIndex],
              content: accumulatedContent || "Completed",
              status: "done",
            };
          }
          return newMessages;
        });
      }

      // Notify HermesPage to refresh sessions list
      window.dispatchEvent(new CustomEvent('hermes-message-sent'));

    } catch (err: any) {
      setError(err.message);
      setMessages(prev => {
        const newMessages = [...prev];
        if (newMessages[msgIndex]) {
          newMessages[msgIndex] = {
            ...newMessages[msgIndex],
            content: `❌ Error: ${err.message}`,
            status: "error",
            error: err.message,
          };
        }
        return newMessages;
      });
    } finally {
      setRunning(false);
    }
  }, [input, running, model, sessionId, messages.length, error]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div style={{
      display: "flex",
      flexDirection: "column",
      height: "100%",
      background: "#0f172a",
      color: "#e2e8f0",
    }}>
      {/* Header with model selection and toolsets */}
      <div style={{
        padding: "0.75rem 1rem",
        borderBottom: "1px solid #1e293b",
        display: "flex",
        flexWrap: "wrap",
        gap: "0.75rem",
        alignItems: "center",
      }}>
        {/* Model selector */}
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
          <span style={{ fontSize: "0.7rem", color: "#64748b" }}>Model:</span>
          <select
            value={model}
            onChange={(e) => setModel(e.target.value)}
            disabled={running}
            style={{
              background: "#1e293b",
              color: "#e2e8f0",
              border: "1px solid #334569",
              borderRadius: "0.375rem",
              padding: "0.25rem 0.5rem",
              fontSize: "0.75rem",
            }}
          >
            {models.map((m) => (
              <option key={m.id} value={m.id}>{m.name}</option>
            ))}
          </select>
        </div>

        {/* Toolset toggles */}
        <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
          {HERMES_TOOLSETS.map((toolset) => (
            <button
              key={toolset.id}
              onClick={() => toggleToolset(toolset.id)}
              disabled={running || toolset.disabled}
              style={{
                padding: "0.25rem 0.5rem",
                fontSize: "0.7rem",
                borderRadius: "0.375rem",
                border: "1px solid",
                cursor: toolset.disabled ? "not-allowed" : "pointer",
                opacity: toolset.disabled ? 0.5 : enabledToolsets.includes(toolset.id) ? 1 : 0.6,
                borderColor: enabledToolsets.includes(toolset.id) ? "#22c55e" : "#334569",
                background: enabledToolsets.includes(toolset.id) ? "#052e16" : "#1e293b",
                color: enabledToolsets.includes(toolset.id) ? "#22c55e" : "#94a3b8",
              }}
            >
              {toolset.label}
            </button>
          ))}
        </div>

        {/* Connection status */}
        <div style={{
          marginLeft: "auto",
          display: "flex",
          alignItems: "center",
          gap: "0.5rem",
          fontSize: "0.65rem",
          color: "#475569",
        }}>
          <span
            style={{
              width: "8px",
              height: "8px",
              borderRadius: "50%",
              background: wsConnected ? "#22c55e" : "#ef4444",
              boxShadow: wsConnected ? "0 0 4px #22c55e" : "none",
            }}
            title={wsConnected ? "Connected" : "Disconnected"}
          />
          <span>{wsConnected ? "Online" : "Offline"}</span>
          {/* 测试按钮 - 按 6 次输入框激活 */}
          {showTestBtn && (
            <>
              {/* 隐藏的文件输入框 */}
              <input
                ref={fileInputRef}
                type="file"
                accept="image/*"
                style={{ display: 'none' }}
                onChange={handleFileSelect}
              />
              <button
                onClick={testImageDisplay}
                style={{
                  padding: "0.2rem 0.4rem",
                  fontSize: "0.6rem",
                  background: "#9333ea",
                  color: "#fff",
                  border: "none",
                  borderRadius: "0.25rem",
                  cursor: "pointer",
                }}
              >
                🧪 Test Images
              </button>
            </>
          )}
        </div>
      </div>

      {/* Error display */}
      {error && (
        <div style={{
          padding: "0.5rem 1rem",
          background: "#450a0a",
          color: "#fca5a5",
          fontSize: "0.75rem",
          borderBottom: "1px solid #7f1d1d",
        }}>
          ❌ {error}
        </div>
      )}

      {/* Approval Dialog */}
      {pendingApproval && (
        <div style={{
          padding: "1rem",
          background: "#1a0a00",
          borderBottom: "2px solid #f59e0b",
        }}>
          <div style={{
            display: "flex",
            alignItems: "center",
            gap: "0.5rem",
            marginBottom: "0.5rem",
            color: "#f59e0b",
            fontWeight: "bold",
          }}>
            <span style={{ fontSize: "1.5rem" }}>⚠️</span>
            <span>危险操作确认</span>
          </div>
          <div style={{
            background: "#1e293b",
            borderRadius: "0.5rem",
            padding: "0.75rem",
            marginBottom: "0.75rem",
          }}>
            <div style={{ color: "#94a3b8", fontSize: "0.7rem", marginBottom: "0.25rem" }}>
              命令：
            </div>
            <code style={{ color: "#22c55e", fontSize: "0.85rem" }}>
              {pendingApproval.command}
            </code>
            {pendingApproval.description && (
              <>
                <div style={{ color: "#94a3b8", fontSize: "0.7rem", marginTop: "0.5rem", marginBottom: "0.25rem" }}>
                  描述：
                </div>
                <div style={{ color: "#e2e8f0", fontSize: "0.8rem", whiteSpace: "pre-wrap" }}>
                  {pendingApproval.description}
                </div>
              </>
            )}
          </div>
          <div style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}>
            <div style={{ color: "#64748b", fontSize: "0.75rem" }}>
              超时: {approvalCountdown}s
            </div>
            <div style={{ display: "flex", gap: "0.5rem" }}>
              <button
                onClick={() => handleApprovalResponse("approve")}
                style={{
                  padding: "0.5rem 1rem",
                  background: "#22c55e",
                  color: "#fff",
                  border: "none",
                  borderRadius: "0.375rem",
                  cursor: "pointer",
                  fontSize: "0.8rem",
                }}
              >
                批准 (once)
              </button>
              <button
                onClick={() => handleApprovalResponse("session")}
                style={{
                  padding: "0.5rem 1rem",
                  background: "#3b82f6",
                  color: "#fff",
                  border: "none",
                  borderRadius: "0.375rem",
                  cursor: "pointer",
                  fontSize: "0.8rem",
                }}
              >
                本次批准
              </button>
              <button
                onClick={() => handleApprovalResponse("deny")}
                style={{
                  padding: "0.5rem 1rem",
                  background: "#ef4444",
                  color: "#fff",
                  border: "none",
                  borderRadius: "0.375rem",
                  cursor: "pointer",
                  fontSize: "0.8rem",
                }}
              >
                拒绝
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Messages area */}
      <div style={{
        flex: 1,
        overflow: "auto",
        padding: "1rem",
      }}>
        {messages.length === 0 && (
          <div style={{
            textAlign: "center",
            color: "#475569",
            padding: "2rem",
            fontSize: "0.85rem",
          }}>
            <div style={{ fontSize: "2rem", marginBottom: "0.5rem" }}>🤖</div>
            <div>Hermes Agent ready</div>
            <div style={{ fontSize: "0.75rem", marginTop: "0.5rem" }}>
              Ask questions about quantum experiments, qubit calibration, or general tasks.
            </div>
          </div>
        )}

        {messages.map((msg, i) => (
          <div key={i} style={{ marginBottom: "1rem" }}>
            {msg.role === "user" && (
              <div style={{
                background: "#1e3a5f",
                borderRadius: "0.75rem",
                padding: "0.75rem 1rem",
                maxWidth: "85%",
                marginLeft: "auto",
                fontSize: "0.85rem",
                lineHeight: 1.5,
              }}>
                {msg.content}
              </div>
            )}

            {msg.role === "assistant" && (
              <div style={{
                background: "#1e293b",
                borderRadius: "0.75rem",
                padding: "0.75rem 1rem",
                maxWidth: "85%",
                fontSize: "0.85rem",
                lineHeight: 1.5,
              }}>
                {/* Thinking process - collapsible */}
                <ReasoningBlock content={msg.thinking || ""} />

                {/* Message content with markdown rendering */}
                <MarkdownContent content={msg.content || ""} />

                {/* Tool calls - collapsible cards */}
                {msg.tool_calls && msg.tool_calls.length > 0 && (
                  <ToolCallsList toolCalls={msg.tool_calls} />
                )}

                {/* Tool Execution Logs */}
                {msg.toolLogs && msg.toolLogs.length > 0 && (
                  <div style={{ marginTop: "0.75rem" }}>
                    <div style={{ color: "#64748b", fontSize: "0.7rem", marginBottom: "0.25rem" }}>
                      📋 Tool Logs ({msg.toolLogs.length}):
                    </div>
                    <div style={{
                      background: "#0f172a",
                      borderRadius: "0.375rem",
                      padding: "0.5rem",
                      fontSize: "0.7rem",
                      fontFamily: "monospace",
                      maxHeight: "300px",
                      overflow: "auto",
                      border: "1px solid #334155",
                    }}>
                      {msg.toolLogs.map((log, idx) => (
                        <div key={idx} style={{
                          padding: "0.25rem 0",
                          borderBottom: idx < msg.toolLogs!.length - 1 ? "1px solid #1e293b" : "none",
                          color: log.type === "start" ? "#38bdf8" : log.is_error ? "#ef4444" : "#22c55e",
                        }}>
                          {log.type === "start" ? (
                            <span>📞 {log.name}({log.args ? Object.keys(log.args).join(", ") : ""})</span>
                          ) : (
                            <span>✅ {log.name} completed in {log.duration?.toFixed(2)}s{log.result_preview ? ` - ${log.result_preview}` : ""}</span>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Images from tool results */}
                {msg.images && msg.images.length > 0 && (
                  <div style={{ marginTop: "0.75rem" }}>
                    <div style={{ color: "#64748b", fontSize: "0.7rem", marginBottom: "0.25rem" }}>
                      🖼️ Images ({msg.images.length}):
                    </div>
                    {msg.images.map((img, idx) => (
                      <ImageDisplay key={idx} img={img} />
                    ))}
                  </div>
                )}

                {msg.status === "error" && (
                  <div style={{ color: "#ef4444", fontSize: "0.75rem", marginTop: "0.5rem" }}>
                    ❌ Error
                  </div>
                )}
              </div>
            )}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      {/* Input area */}
      <div style={{
        padding: "0.75rem 1rem",
        borderTop: "1px solid #1e293b",
        display: "flex",
        gap: "0.5rem",
      }}>
        <textarea
          value={input}
          onChange={(e) => {
            setInput(e.target.value);
            // 敲6次激活测试按钮
            if (!showTestBtn) {
              setInputCount(prev => {
                const next = prev + 1;
                if (next >= 5) setShowTestBtn(true);
                return next;
              });
            }
          }}
          onKeyDown={handleKeyDown}
          placeholder="Ask about quantum experiments, qubit calibration..."
          disabled={running}
          style={{
            flex: 1,
            background: "#1e293b",
            border: "1px solid #334569",
            borderRadius: "0.5rem",
            padding: "0.5rem 0.75rem",
            color: "#e2e8f0",
            fontSize: "0.85rem",
            resize: "none",
            minHeight: "2.5rem",
            maxHeight: "6rem",
            fontFamily: "inherit",
          }}
          rows={1}
        />
        <button
          onClick={handleSend}
          disabled={running || !input.trim()}
          style={{
            padding: "0.5rem 1rem",
            background: running ? "#334569" : "#3b82f6",
            color: "#fff",
            border: "none",
            borderRadius: "0.5rem",
            cursor: running ? "not-allowed" : "pointer",
            fontSize: "0.85rem",
            fontWeight: 500,
          }}
        >
          {running ? "..." : "Send"}
        </button>
      </div>
    </div>
  );
}
