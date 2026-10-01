import {
  CircleHelp,
  Database,
  FileClock,
  Film,
  Layers3,
  Library,
  LayoutDashboard,
  RefreshCw,
  Settings,
} from "lucide-react";
import { formatBytes, formatNumber } from "../lib/format";


const NAV_ITEMS = [
  { id: "dashboard", label: "仪表盘", icon: LayoutDashboard },
  { id: "library", label: "资料库", icon: Library },
  { id: "pending", label: "待匹配", icon: CircleHelp, countKey: "pending" },
  { id: "duplicates", label: "多来源", icon: Layers3, countKey: "multi" },
  { id: "imports", label: "入库记录", icon: FileClock },
  { id: "settings", label: "设置", icon: Settings },
];


export default function Sidebar({ view, onChange, stats, config }) {
  const counts = {
    pending: stats?.media?.pending ?? 0,
    multi: stats?.multi_source ?? 0,
  };

  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="brand-mark"><Film size={22} strokeWidth={1.8} /></span>
        <div>
          <strong>影库</strong>
          <span>LOCAL 1.0</span>
        </div>
      </div>

      <nav className="sidebar-nav" aria-label="主导航">
        {NAV_ITEMS.map((item) => {
          const Icon = item.icon;
          const count = item.countKey ? counts[item.countKey] : null;
          return (
            <button
              key={item.id}
              type="button"
              className={view === item.id ? "active" : ""}
              onClick={() => onChange(item.id)}
            >
              <Icon size={18} />
              <span>{item.label}</span>
              {count ? <small>{formatNumber(count)}</small> : null}
            </button>
          );
        })}
      </nav>

      <div className="sidebar-summary">
        <span className="summary-label">本地资料</span>
        <strong>{formatNumber(stats?.media?.total)} 部作品</strong>
        <span>{formatNumber(stats?.sources?.total)} 条来源</span>
        <div className="summary-grid">
          <span>115</span><b>{formatNumber(stats?.sources?.share_115)}</b>
          <span>ED2K</span><b>{formatNumber(stats?.sources?.ed2k)}</b>
          <span>文件体积</span><b>{formatBytes(stats?.sources?.bytes)}</b>
        </div>
      </div>

      <div className={`sidebar-tmdb ${config?.tmdb_configured ? "connected" : "offline"}`} title={config?.tmdb_auth_mode ? `认证方式：${config.tmdb_auth_mode}` : "未配置 TMDB"}>
        <i aria-hidden="true" />
        <span>{config?.tmdb_configured ? "TMDB 已连接" : "TMDB 未配置"}</span>
      </div>

      <div className="sidebar-footer" aria-label="辅助操作">
        <button type="button" title="刷新"><RefreshCw size={17} /></button>
        <button type="button" title="数据库"><Database size={17} /></button>
        <button type="button" title="帮助"><CircleHelp size={17} /></button>
      </div>
    </aside>
  );
}
