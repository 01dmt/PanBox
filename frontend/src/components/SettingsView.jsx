import { Check, CheckCircle2, CloudOff, Copy, Database, KeyRound, RefreshCw, ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";
import { formatNumber } from "../lib/format";
import { getIngestionConfig, saveIngestionConfig } from "../api";


export default function SettingsView({ config, stats }) {
  const [ingestion, setIngestion] = useState(null);
  const [apiKey, setApiKey] = useState("");
  const [revealed, setRevealed] = useState("");
  const [copied, setCopied] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  useEffect(() => { getIngestionConfig().then(setIngestion).catch(() => setIngestion({ configured: false, source: "none" })); }, []);
  const generate = () => { const value = `ing_${crypto.randomUUID().replaceAll("-", "")}${crypto.randomUUID().replaceAll("-", "")}`; setApiKey(value); setRevealed(value); setCopied(false); };
  const save = async () => { setSaving(true); setMessage(""); try { await saveIngestionConfig(apiKey.trim()); setIngestion({ configured: true, source: "file" }); setRevealed(apiKey.trim()); setApiKey(""); setMessage("已保存。请立即复制并保存此 Key，页面不会再次回显完整内容。"); } catch (error) { setMessage(error.message); } finally { setSaving(false); } };
  const copy = async () => { if (!revealed) return; await navigator.clipboard.writeText(revealed); setCopied(true); };
  return (
    <main className="page-view settings-view">
      <section className="settings-section">
        <div className="settings-heading"><KeyRound size={20} /><div><h2>TMDB 连接</h2><span>外部元数据提供方</span></div></div>
        <div className={`connection-row ${config?.tmdb_configured ? "ok" : "missing"}`}>
          {config?.tmdb_configured ? <CheckCircle2 size={20} /> : <CloudOff size={20} />}
          <div>
            <strong>{config?.tmdb_configured ? "连接凭据已加载" : "尚未配置凭据"}</strong>
            <span>{config?.tmdb_auth_mode === "bearer" ? "TMDB_API_TOKEN" : config?.tmdb_auth_mode === "api_key" ? "TMDB_API_KEY" : "在项目根目录 .env 中填写 TMDB_API_TOKEN"}</span>
          </div>
        </div>
        <div className="settings-code"><code>TMDB_API_TOKEN=your_v4_read_access_token</code></div>
      </section>

      <section className="settings-section ingestion-key-settings">
        <div className="settings-heading"><ShieldCheck size={20} /><div><h2>外部消息接入</h2><span>保护原始消息 Webhook</span></div></div>
        <div className={`connection-row ${ingestion?.configured ? "ok" : "missing"}`}><KeyRound size={20} /><div><strong>{ingestion?.configured ? "接入 Key 已配置" : "尚未配置接入 Key"}</strong><span>{ingestion?.source === "file" ? "本地安全文件（600 权限）" : ingestion?.source === "env" ? "环境变量 INGESTION_API_KEY" : "外部项目无法推送消息"}</span></div></div>
        <label className="ingestion-key-label">新的接入 API Key<input type="password" value={apiKey} placeholder="输入或生成至少 16 位 Key" onChange={(event) => setApiKey(event.target.value)} /></label>
        <div className="ingestion-key-actions"><button type="button" className="button" onClick={generate}><RefreshCw size={14} />随机生成</button><button type="button" className="button primary" disabled={saving || apiKey.trim().length < 16} onClick={save}>{saving ? "保存中…" : "保存 Key"}</button></div>
        {revealed ? <div className="ingestion-key-reveal" role="status"><strong>{message || "请立即复制并保存此 API Key，刷新或离开本页后将不再完整显示。"}</strong><code>{revealed}</code><div><button type="button" className="button small" onClick={copy}>{copied ? <Check size={14} /> : <Copy size={14} />}{copied ? "已复制" : "复制 Key"}</button><button type="button" className="text-command" onClick={() => setRevealed("")}>关闭</button></div></div> : message ? <p className="settings-feedback" role="status">{message}</p> : null}
      </section>

      <section className="settings-section">
        <div className="settings-heading"><Database size={20} /><div><h2>SQLite 数据库</h2><span>本地持久化</span></div></div>
        <dl className="settings-list">
          <div><dt>文件位置</dt><dd>{config?.db_path || "data/media.db"}</dd></div>
          <div><dt>媒体作品</dt><dd>{formatNumber(stats?.media?.total)}</dd></div>
          <div><dt>来源记录</dt><dd>{formatNumber(stats?.sources?.total)}</dd></div>
          <div><dt>导入批次</dt><dd>{formatNumber(stats?.imports)}</dd></div>
        </dl>
      </section>
    </main>
  );
}
