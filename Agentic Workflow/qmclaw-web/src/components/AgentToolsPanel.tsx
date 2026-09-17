"use client";

/**
 * Agent Tools Panel — MCP tools and Skills management
 */

import { useState, useEffect, useRef } from "react";
import { api } from "../lib/api";

type SubTab = "mcp" | "skills";

// ── Types ─────────────────────────────────────────────────────────────────────

interface McpServer {
  id: string;
  name: string;
  transport: string;
  url: string;
  enabled: boolean;
}

interface McpTool {
  id: string;
  server: string;
  remote_name: string;
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
  enabled: boolean;
}

interface Skill {
  id: string;
  name: string;
  description: string;
  skill_file?: string;
  trigger_keywords: string[];
  steps: { tool: string; input: Record<string, unknown> }[];
  parameters: string[];
  createdAt: string;
  enabled: boolean;
}

interface SkillMatch {
  id: string;
  name: string;
  description: string;
  trigger_keywords: string[];
}

// ── MCP Tools Sub-panel ───────────────────────────────────────────────────────

function McpToolsPanel() {
  const [servers, setServers] = useState<McpServer[]>([]);
  const [tools, setTools] = useState<McpTool[]>([]);
  const [loading, setLoading] = useState(true);
  const [showServerForm, setShowServerForm] = useState(false);
  const [showToolForm, setShowToolForm] = useState(false);
  const [editingServer, setEditingServer] = useState<McpServer | null>(null);
  const [editingTool, setEditingTool] = useState<McpTool | null>(null);
  const [testResult, setTestResult] = useState<string>("");
  const [testing, setTesting] = useState(false);
  const [exportAnchor, setExportAnchor] = useState<HTMLAnchorElement | null>(null);

  const [serverForm, setServerForm] = useState({ id: "", name: "", transport: "streamable-http", url: "", enabled: true });
  const [toolForm, setToolForm] = useState({ id: "", server: "", remote_name: "", name: "", description: "", enabled: true, input_schema: "{}" });

  function load() {
    setLoading(true);
    api.getMcpTools().then((res: any) => {
      setServers(res.mcp_servers || []);
      setTools(res.mcp_tools || []);
      setLoading(false);
    });
  }

  useEffect(() => { load(); }, []);

  async function handleSaveServer() {
    const data = { ...serverForm, transport: serverForm.transport || "streamable-http" };
    if (editingServer) {
      await api.updateMcpTool(data.id, data);
    } else {
      await api.createMcpTool({ ...data, _type: "server" });
    }
    setShowServerForm(false);
    setEditingServer(null);
    setServerForm({ id: "", name: "", transport: "streamable-http", url: "", enabled: true });
    load();
  }

  async function handleSaveTool() {
    let input_schema: Record<string, unknown> = {};
    try { input_schema = JSON.parse(toolForm.input_schema); } catch { /* use empty */ }
    const data = { ...toolForm, input_schema };
    if (editingTool) {
      await api.updateMcpTool(data.id, data);
    } else {
      await api.createMcpTool(data);
    }
    setShowToolForm(false);
    setEditingTool(null);
    setToolForm({ id: "", server: "", remote_name: "", name: "", description: "", enabled: true, input_schema: "{}" });
    load();
  }

  async function handleDelete(id: string) {
    if (!confirm("Delete this item?")) return;
    await api.deleteMcpTool(id);
    load();
  }

  async function handleTestTool(tool: McpTool) {
    setTesting(true);
    setTestResult("Calling...");
    try {
      const res = await fetch(`http://localhost:3002/api/mcp-tools/${tool.id}/test`, { method: "POST" });
      const data = await res.json();
      setTestResult(JSON.stringify(data, null, 2));
    } catch (e: any) {
      setTestResult(`Error: ${e.message}`);
    } finally {
      setTesting(false);
    }
  }

  async function handleExport() {
    const res = await api.getMcpTools();
    const blob = new Blob([JSON.stringify(res, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "mcp_tools.json";
    a.click();
    URL.revokeObjectURL(url);
  }

  if (loading) return <div style={{ color: "#64748b", padding: "20px" }}>Loading...</div>;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "16px", height: "100%", overflow: "auto" }}>
      {/* Toolbar */}
      <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
        <button onClick={() => { setEditingServer(null); setServerForm({ id: "", name: "", transport: "streamable-http", url: "", enabled: true }); setShowServerForm(true); }} style={btnStyle("#0369a1")}>+ Server</button>
        <button onClick={() => { setEditingTool(null); setToolForm({ id: "", server: servers[0]?.id || "", remote_name: "", name: "", description: "", enabled: true, input_schema: "{}" }); setShowToolForm(true); }} style={btnStyle("#0369a1")}>+ Tool</button>
        <button onClick={handleExport} style={btnStyle("#334155")}>导出 JSON</button>
      </div>

      {/* Server list */}
      {servers.length > 0 && (
        <div>
          <div style={{ fontSize: "11px", color: "#475569", marginBottom: "6px", fontWeight: 700 }}>MCP SERVERS</div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: "8px" }}>
            {servers.map(srv => (
              <div key={srv.id} style={{ background: "#1e293b", border: "1px solid #334155", borderRadius: "8px", padding: "12px" }}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "6px" }}>
                  <span style={{ fontWeight: 700, color: "#e2e8f0", fontSize: "13px" }}>{srv.name}</span>
                  <span style={{ fontSize: "10px", padding: "1px 6px", borderRadius: "4px", background: srv.enabled ? "#22c55e20" : "#334155", color: srv.enabled ? "#22c55e" : "#64748b" }}>
                    {srv.enabled ? "ON" : "OFF"}
                  </span>
                </div>
                <div style={{ fontSize: "10px", color: "#64748b", fontFamily: "monospace" }}>{srv.url}</div>
                <div style={{ fontSize: "10px", color: "#475569", marginTop: "4px" }}>{srv.transport}</div>
                <div style={{ display: "flex", gap: "6px", marginTop: "8px" }}>
                  <button onClick={() => { setEditingServer(srv); setServerForm({ id: srv.id, name: srv.name, transport: srv.transport, url: srv.url, enabled: srv.enabled }); setShowServerForm(true); }} style={btnStyle("#334155")}>Edit</button>
                  <button onClick={() => handleDelete(srv.id)} style={{ ...btnStyle("#7f1d1d"), padding: "4px 8px", fontSize: "10px" }}>Del</button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Tool list */}
      {tools.length > 0 && (
        <div>
          <div style={{ fontSize: "11px", color: "#475569", marginBottom: "6px", fontWeight: 700 }}>MCP TOOLS ({tools.length})</div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: "8px" }}>
            {tools.map(tool => (
              <div key={tool.id} style={{ background: "#0f172a", border: "1px solid #1e293b", borderRadius: "8px", padding: "12px" }}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "4px" }}>
                  <span style={{ fontWeight: 700, color: "#38bdf8", fontSize: "12px", fontFamily: "monospace" }}>{tool.name}</span>
                  <span style={{ fontSize: "10px", color: "#64748b" }}>{tool.remote_name}</span>
                </div>
                <div style={{ fontSize: "11px", color: "#94a3b8", marginBottom: "6px" }}>{tool.description}</div>
                <div style={{ fontSize: "10px", color: "#475569", marginBottom: "8px" }}>server: {tool.server}</div>
                <div style={{ display: "flex", gap: "6px" }}>
                  <button onClick={() => handleTestTool(tool)} disabled={testing} style={{ ...btnStyle("#334155"), padding: "4px 8px", fontSize: "10px" }}>Test</button>
                  <button onClick={() => { setEditingTool(tool); setToolForm({ id: tool.id, server: tool.server, remote_name: tool.remote_name, name: tool.name, description: tool.description, enabled: tool.enabled, input_schema: JSON.stringify(tool.input_schema, null, 2) }); setShowToolForm(true); }} style={btnStyle("#334155")}>Edit</button>
                  <button onClick={() => handleDelete(tool.id)} style={{ ...btnStyle("#7f1d1d"), padding: "4px 8px", fontSize: "10px" }}>Del</button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {servers.length === 0 && tools.length === 0 && (
        <div style={{ textAlign: "center", color: "#475569", padding: "40px" }}>
          <div style={{ fontSize: "24px", marginBottom: "8px" }}>🔌</div>
          <div style={{ fontSize: "13px" }}>No MCP tools configured</div>
          <div style={{ fontSize: "11px", color: "#334155", marginTop: "4px" }}>Add a server and tools to extend agent capabilities</div>
        </div>
      )}

      {/* Test result */}
      {testResult && (
        <div style={{ background: "#0f172a", border: "1px solid #22c55e40", borderRadius: "8px", padding: "12px" }}>
          <div style={{ fontSize: "11px", color: "#22c55e", fontWeight: 700, marginBottom: "6px" }}>TEST RESULT</div>
          <pre style={{ margin: 0, fontSize: "10px", color: "#94a3b8", fontFamily: "monospace", whiteSpace: "pre-wrap", maxHeight: "150px", overflow: "auto" }}>{testResult}</pre>
          <button onClick={() => setTestResult("")} style={{ ...btnStyle("#334155"), marginTop: "8px", padding: "4px 8px", fontSize: "10px" }}>Clear</button>
        </div>
      )}

      {/* Server form modal */}
      {showServerForm && (
        <Modal title={editingServer ? "Edit Server" : "Add Server"} onClose={() => setShowServerForm(false)}>
          <FormField label="ID"><input value={serverForm.id} onChange={e => setServerForm({ ...serverForm, id: e.target.value })} disabled={!!editingServer} style={inputStyle} /></FormField>
          <FormField label="Name"><input value={serverForm.name} onChange={e => setServerForm({ ...serverForm, name: e.target.value })} style={inputStyle} /></FormField>
          <FormField label="URL"><input value={serverForm.url} onChange={e => setServerForm({ ...serverForm, url: e.target.value })} placeholder="http://localhost:8008" style={inputStyle} /></FormField>
          <FormField label="Transport"><select value={serverForm.transport} onChange={e => setServerForm({ ...serverForm, transport: e.target.value })} style={{ ...inputStyle, background: "#0f172a" }}><option value="streamable-http">streamable-http</option></select></FormField>
          <FormField label="Enabled">
            <label style={{ display: "flex", alignItems: "center", gap: "6px", cursor: "pointer" }}>
              <input type="checkbox" checked={serverForm.enabled} onChange={e => setServerForm({ ...serverForm, enabled: e.target.checked })} />
              <span style={{ fontSize: "12px", color: "#94a3b8" }}>Enabled</span>
            </label>
          </FormField>
          <div style={{ display: "flex", gap: "8px", marginTop: "8px" }}>
            <button onClick={handleSaveServer} style={btnStyle("#0369a1")}>Save</button>
            <button onClick={() => setShowServerForm(false)} style={btnStyle("#334155")}>Cancel</button>
          </div>
        </Modal>
      )}

      {/* Tool form modal */}
      {showToolForm && (
        <Modal title={editingTool ? "Edit Tool" : "Add Tool"} onClose={() => setShowToolForm(false)}>
          <FormField label="ID"><input value={toolForm.id} onChange={e => setToolForm({ ...toolForm, id: e.target.value })} disabled={!!editingTool} style={inputStyle} /></FormField>
          <FormField label="Server"><select value={toolForm.server} onChange={e => setToolForm({ ...toolForm, server: e.target.value })} style={{ ...inputStyle, background: "#0f172a" }}>{servers.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select></FormField>
          <FormField label="Remote Name"><input value={toolForm.remote_name} onChange={e => setToolForm({ ...toolForm, remote_name: e.target.value })} placeholder="e.g. s21, t1, rabi" style={inputStyle} /></FormField>
          <FormField label="Display Name"><input value={toolForm.name} onChange={e => setToolForm({ ...toolForm, name: e.target.value })} placeholder="e.g. 腔体 S21 测量" style={inputStyle} /></FormField>
          <FormField label="Description"><textarea value={toolForm.description} onChange={e => setToolForm({ ...toolForm, description: e.target.value })} style={{ ...inputStyle, resize: "vertical" }} rows={2} /></FormField>
          <FormField label="Input Schema (JSON)"><textarea value={toolForm.input_schema} onChange={e => setToolForm({ ...toolForm, input_schema: e.target.value })} style={{ ...inputStyle, fontFamily: "monospace", fontSize: "11px", resize: "vertical" }} rows={4} /></FormField>
          <FormField label="Enabled">
            <label style={{ display: "flex", alignItems: "center", gap: "6px", cursor: "pointer" }}>
              <input type="checkbox" checked={toolForm.enabled} onChange={e => setToolForm({ ...toolForm, enabled: e.target.checked })} />
              <span style={{ fontSize: "12px", color: "#94a3b8" }}>Enabled</span>
            </label>
          </FormField>
          <div style={{ display: "flex", gap: "8px", marginTop: "8px" }}>
            <button onClick={handleSaveTool} style={btnStyle("#0369a1")}>Save</button>
            <button onClick={() => setShowToolForm(false)} style={btnStyle("#334155")}>Cancel</button>
          </div>
        </Modal>
      )}
    </div>
  );
}

// ── Skills Sub-panel ───────────────────────────────────────────────────────────

type SkillFormState = {
  name: string;
  description: string;
  trigger_keywords: string;
  parameters: string;
  steps: { tool: string; input: Record<string, unknown> }[];
  enabled: boolean;
};

function skillToFormState(skill: Skill): SkillFormState {
  return {
    name: skill.name,
    description: skill.description || "",
    trigger_keywords: (skill.trigger_keywords || []).join(", "),
    parameters: (skill.parameters || []).join(", "),
    steps: skill.steps || [],
    enabled: skill.enabled !== false,
  };
}

function SkillsPanel() {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editingSkill, setEditingSkill] = useState<Skill | null>(null);
  const [matchInput, setMatchInput] = useState("");
  const [matchResults, setMatchResults] = useState<SkillMatch[]>([]);
  const [executeSkillId, setExecuteSkillId] = useState("");
  const [executeParams, setExecuteParams] = useState("");
  const [executeResult, setExecuteResult] = useState<string>("");
  const [executing, setExecuting] = useState(false);

  const [form, setForm] = useState<SkillFormState>({
    name: "", description: "", trigger_keywords: "", parameters: "qubit",
    steps: [{ tool: "run_experiment", input: { qubit: "{{qubit}}", fn: "sq.t1" } }],
    enabled: true,
  });

  function load() {
    setLoading(true);
    api.getSkills().then((res: any) => {
      setSkills(res.skills || []);
      setLoading(false);
    });
  }

  useEffect(() => { load(); }, []);

  async function handleSave() {
    const skill: Record<string, unknown> = {
      name: form.name,
      description: form.description,
      trigger_keywords: form.trigger_keywords.split(",").map(s => s.trim()).filter(Boolean),
      parameters: form.parameters.split(",").map(s => s.trim()).filter(Boolean),
      steps: form.steps,
      enabled: true,
    };
    if (editingSkill) {
      await api.updateSkill(editingSkill.id, skill);
    } else {
      await api.createSkill(skill);
    }
    setShowForm(false);
    setEditingSkill(null);
    load();
  }

  async function handleDelete(id: string) {
    if (!confirm("Delete this skill?")) return;
    await api.deleteSkill(id);
    load();
  }

  async function handleMatch() {
    if (!matchInput.trim()) return;
    const res: any = await api.matchSkills(matchInput);
    setMatchResults(res.matched || []);
  }

  async function handleExecute() {
    if (!executeSkillId) return;
    setExecuting(true);
    setExecuteResult("");
    try {
      const params: Record<string, string> = {};
      for (const pair of executeParams.split(",").map(s => s.trim())) {
        const [k, v] = pair.split(":");
        if (k && v) params[k.trim()] = v.trim();
      }
      const res = await api.executeSkill(executeSkillId, params);
      setExecuteResult(JSON.stringify(res, null, 2));
    } catch (e: any) {
      setExecuteResult(`Error: ${e.message}`);
    } finally {
      setExecuting(false);
    }
  }

  async function handleExport() {
    const res = await api.exportSkills();
    const blob = new Blob([JSON.stringify(res, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "skills.json";
    a.click();
    URL.revokeObjectURL(url);
  }

  async function handleImport(file: File) {
    const text = await file.text();
    let data: { skills?: unknown[] };
    try { data = JSON.parse(text); } catch { alert("Invalid JSON"); return; }
    if (Array.isArray(data.skills)) {
      await api.importSkills(data.skills);
      load();
    } else {
      alert("skills array not found in file");
    }
  }

  if (loading) return <div style={{ color: "#64748b", padding: "20px" }}>Loading...</div>;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "16px", height: "100%", overflow: "auto" }}>
      {/* Toolbar */}
      <div style={{ display: "flex", gap: "8px", flexWrap: "wrap", alignItems: "center" }}>
        <button onClick={() => { setEditingSkill(null); setForm({ name: "", description: "", trigger_keywords: "", parameters: "qubit", steps: [{ tool: "run_experiment", input: { qubit: "{{qubit}}", fn: "sq.t1" } }], enabled: true }); setShowForm(true); }} style={btnStyle("#0369a1")}>+ Skill</button>
        <button onClick={handleExport} style={btnStyle("#334155")}>导出 JSON</button>
        <label style={{ ...btnStyle("#334155"), padding: "6px 12px", cursor: "pointer" }}>
          导入 JSON
          <input type="file" accept=".json" style={{ display: "none" }} onChange={e => { const f = e.target.files?.[0]; if (f) handleImport(f); }} />
        </label>
      </div>

      {/* Match test */}
      <div style={{ background: "#1e293b", borderRadius: "8px", padding: "12px", border: "1px solid #334155" }}>
        <div style={{ fontSize: "11px", color: "#475569", marginBottom: "8px", fontWeight: 700 }}>TRIGGER MATCH TEST</div>
        <div style={{ display: "flex", gap: "8px" }}>
          <input value={matchInput} onChange={e => setMatchInput(e.target.value)} placeholder="输入测试消息，如：对 q10lu1 做 T1 健康检查"
            style={{ ...inputStyle, flex: 1 }} onKeyDown={e => e.key === "Enter" && handleMatch()} />
          <button onClick={handleMatch} style={btnStyle("#7c3aed")}>测试</button>
        </div>
        {matchResults.length > 0 && (
          <div style={{ marginTop: "8px", display: "flex", flexDirection: "column", gap: "4px" }}>
            {matchResults.map(r => <div key={r.id} style={{ fontSize: "11px", color: "#22c55e" }}>✓ {r.name} — {r.description}</div>)}
          </div>
        )}
      </div>

      {/* Skill list */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))", gap: "10px" }}>
        {skills.map(skill => (
          <div key={skill.id} style={{ background: "#0f172a", border: "1px solid #1e293b", borderRadius: "8px", padding: "12px" }}>
            <div style={{ fontWeight: 700, color: "#e2e8f0", fontSize: "13px", marginBottom: "4px" }}>{skill.name}</div>
            <div style={{ fontSize: "11px", color: "#94a3b8", marginBottom: "8px" }}>{skill.description}</div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "4px", marginBottom: "8px" }}>
              {(skill.trigger_keywords || []).map((kw: string) => (
                <span key={kw} style={{ fontSize: "9px", padding: "1px 6px", background: "#7c3aed30", color: "#a78bfa", borderRadius: "4px", fontFamily: "monospace" }}>{kw}</span>
              ))}
            </div>
            <div style={{ fontSize: "10px", color: "#475569", marginBottom: "8px" }}>{skill.steps?.length || 0} steps · params: {(skill.parameters || []).join(", ")}</div>
            <div style={{ display: "flex", gap: "6px", flexWrap: "wrap" }}>
              <button onClick={() => { setExecuteSkillId(skill.id); }} style={{ ...btnStyle("#334155"), padding: "4px 8px", fontSize: "10px" }}>Execute</button>
              <button onClick={() => { setEditingSkill(skill); setForm(skillToFormState(skill)); setShowForm(true); }} style={btnStyle("#334155")}>Edit</button>
              <button onClick={() => handleDelete(skill.id)} style={{ ...btnStyle("#7f1d1d"), padding: "4px 8px", fontSize: "10px" }}>Del</button>
            </div>
          </div>
        ))}
      </div>

      {skills.length === 0 && (
        <div style={{ textAlign: "center", color: "#475569", padding: "40px" }}>
          <div style={{ fontSize: "24px", marginBottom: "8px" }}>🧠</div>
          <div style={{ fontSize: "13px" }}>No skills configured</div>
          <div style={{ fontSize: "11px", color: "#334155", marginTop: "4px" }}>Add a skill to teach the agent new workflows</div>
        </div>
      )}

      {/* Execute */}
      {executeSkillId && (
        <div style={{ background: "#1e293b", borderRadius: "8px", padding: "12px", border: "1px solid #7c3aed40" }}>
          <div style={{ fontSize: "11px", color: "#a78bfa", fontWeight: 700, marginBottom: "8px" }}>EXECUTE SKILL</div>
          <div style={{ fontSize: "11px", color: "#64748b", marginBottom: "6px" }}>skill_id: {executeSkillId}</div>
          <input value={executeParams} onChange={e => setExecuteParams(e.target.value)} placeholder="qubit: q10lu1"
            style={{ ...inputStyle, marginBottom: "8px", width: "100%" }} onKeyDown={e => e.key === "Enter" && handleExecute()} />
          <div style={{ display: "flex", gap: "8px" }}>
            <button onClick={handleExecute} disabled={executing} style={btnStyle("#7c3aed")}>{executing ? "..." : "Execute"}</button>
            <button onClick={() => { setExecuteSkillId(""); setExecuteResult(""); }} style={btnStyle("#334155")}>Cancel</button>
          </div>
          {executeResult && (
            <pre style={{ marginTop: "8px", fontSize: "10px", color: "#22c55e", fontFamily: "monospace", whiteSpace: "pre-wrap", maxHeight: "200px", overflow: "auto", background: "#0f172a", padding: "8px", borderRadius: "6px" }}>
              {executeResult}
            </pre>
          )}
        </div>
      )}

      {/* Skill form modal */}
      {showForm && (
        <Modal title={editingSkill ? "Edit Skill" : "Add Skill"} onClose={() => setShowForm(false)}>
          <FormField label="Name"><input value={form.name || ""} onChange={e => setForm({ ...form, name: e.target.value })} style={inputStyle} /></FormField>
          <FormField label="Description"><textarea value={form.description || ""} onChange={e => setForm({ ...form, description: e.target.value })} style={{ ...inputStyle, resize: "vertical" }} rows={2} /></FormField>
          <FormField label="Trigger Keywords (comma-separated)"><input value={form.trigger_keywords} onChange={e => setForm({ ...form, trigger_keywords: e.target.value })} placeholder="t1, 弛豫时间, health check" style={inputStyle} /></FormField>
          <FormField label="Parameters (comma-separated)"><input value={form.parameters} onChange={e => setForm({ ...form, parameters: e.target.value })} placeholder="qubit" style={inputStyle} /></FormField>
          <FormField label="Steps (one per line, tool:fn:qubit format)">
            <textarea
              value={(form.steps || []).map(s => `${s.tool}:${s.input?.fn || ""}:${s.input?.qubit || ""}`).join("\n")}
              onChange={e => {
                const steps = e.target.value.split("\n").filter(Boolean).map(line => {
                  const parts = line.split(":");
                  return { tool: parts[0] || "run_experiment", input: { fn: parts[1] || "sq.t1", qubit: parts[2] || "{{qubit}}" } };
                });
                setForm({ ...form, steps });
              }}
              style={{ ...inputStyle, fontFamily: "monospace", fontSize: "11px", resize: "vertical" }}
              rows={5}
            />
          </FormField>
          <div style={{ display: "flex", gap: "8px", marginTop: "8px" }}>
            <button onClick={handleSave} style={btnStyle("#0369a1")}>Save</button>
            <button onClick={() => setShowForm(false)} style={btnStyle("#334155")}>Cancel</button>
          </div>
        </Modal>
      )}
    </div>
  );
}

// ── Shared Components ──────────────────────────────────────────────────────────

function Modal({ title, children, onClose }: { title: string; children: React.ReactNode; onClose: () => void }) {
  return (
    <div style={{ position: "fixed", inset: 0, background: "#00000080", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000 }} onClick={e => e.target === e.currentTarget && onClose()}>
      <div style={{ background: "#1e293b", border: "1px solid #334155", borderRadius: "12px", padding: "20px", width: "min(560px, 90vw)", maxHeight: "80vh", overflow: "auto" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px" }}>
          <span style={{ fontWeight: 700, color: "#e2e8f0" }}>{title}</span>
          <button onClick={onClose} style={{ background: "none", border: "none", color: "#64748b", cursor: "pointer", fontSize: "16px" }}>×</button>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>{children}</div>
      </div>
    </div>
  );
}

function FormField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div style={{ fontSize: "11px", color: "#64748b", marginBottom: "4px", fontWeight: 600 }}>{label}</div>
      {children}
    </div>
  );
}

// ── Main Panel ────────────────────────────────────────────────────────────────

export default function AgentToolsPanel() {
  const [subTab, setSubTab] = useState<SubTab>("mcp");

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", gap: "12px" }}>
      {/* Header */}
      <div style={{ background: "#1e293b", borderRadius: "8px", padding: "12px 16px", border: "1px solid #334155", display: "flex", gap: "8px", alignItems: "center" }}>
        <span style={{ fontSize: "14px", fontWeight: 700, color: "#e2e8f0" }}>🛠 Agent Tools</span>
        <div style={{ display: "flex", gap: "4px", marginLeft: "12px" }}>
          {([["mcp", "MCP Tools"], ["skills", "Skills"]] as [SubTab, string][]).map(([tab, label]) => (
            <button key={tab} onClick={() => setSubTab(tab)} style={{
              padding: "4px 12px", borderRadius: "6px", border: "1px solid",
              borderColor: subTab === tab ? "#38bdf8" : "#334155",
              background: subTab === tab ? "#1e3a5f" : "#0f172a",
              color: subTab === tab ? "#38bdf8" : "#64748b",
              fontSize: "11px", fontWeight: 600, cursor: "pointer",
            }}>
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* Content */}
      <div style={{ flex: 1, overflow: "auto" }}>
        {subTab === "mcp" ? <McpToolsPanel /> : <SkillsPanel />}
      </div>
    </div>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────

const btnStyle = (bg: string) => ({
  padding: "6px 12px",
  background: bg,
  border: "none",
  borderRadius: "6px",
  color: "#e2e8f0",
  fontSize: "11px",
  fontWeight: 600,
  cursor: "pointer" as const,
});

const inputStyle: React.CSSProperties = {
  width: "100%",
  background: "#0f172a",
  border: "1px solid #334155",
  borderRadius: "6px",
  padding: "6px 10px",
  color: "#e2e8f0",
  fontSize: "12px",
  outline: "none",
  boxSizing: "border-box" as const,
};
