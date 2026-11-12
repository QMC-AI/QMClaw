/**
 * 模型配置中的环境变量展开（与 services/common/env_expand.py 行为一致）。
 *
 * `config/model_configs.json` 里写 `"baseUrl": "${QMCLAW_DEEPSEEK_BASE_URL}"`，
 * 只有真正发起请求时才展开，避免前端读取/保存模型配置时把内网地址写回 JSON。
 */

const VAR_RE = /\$\{([A-Za-z_][A-Za-z0-9_]*)\}/g;

const DEFAULT_BASE_URLS: Record<string, string> = {
  deepseek: "https://api.deepseek.com/v1",
  openai: "https://api.openai.com/v1",
};

export function expandEnv(value: string): string {
  return value.replace(VAR_RE, (_match, name: string) => process.env[name] ?? "");
}

export function resolveModelBaseUrl(model: {
  baseUrl?: string;
  provider?: string;
}): string {
  const raw = model.baseUrl ?? "";
  if (!raw) return "";

  const expanded = expandEnv(raw).trim();
  if (expanded) return expanded;

  // 占位符没解析出来（变量未设置）→ 回落到 provider 官方地址
  if (raw.includes("${")) {
    return DEFAULT_BASE_URLS[model.provider ?? ""] ?? "";
  }
  return "";
}
