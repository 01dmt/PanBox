import { CheckCircle2, CloudOff, Database, KeyRound } from "lucide-react";
import { formatNumber } from "../lib/format";


export default function SettingsView({ config, stats }) {
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
