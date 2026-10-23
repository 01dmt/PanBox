import {
  CircleHelp,
  Database,
  FileClock,
  Film,
  FolderOpen,
  Layers3,
  Library,
  LayoutDashboard,
  RefreshCw,
  Settings,
} from "lucide-react";
import { Fragment } from "react";
import { formatBytes, formatNumber } from "../lib/format";


const NAV_ITEMS = [
  { id: "dashboard", label: "仪表盘", icon: LayoutDashboard },
  { id: "library", label: "影视库", icon: Library },
  { id: "resources", label: "资源库", icon: Layers3 },
  { id: "series", label: "系列", icon: FolderOpen },
  { id: "imports", label: "入库记录", icon: FileClock },
  { id: "settings", label: "设置", icon: Settings },
];


export default function Sidebar({ view, onChange, stats, config, providerOptions = [] }) {
  const providerCounts = new Map((stats?.sources?.providers || []).map((provider) => [provider.id, provider.count]));
  const providers = providerOptions.length
    ? providerOptions.map((provider) => ({ ...provider, count: providerCounts.get(provider.id) || 0 }))
    : (stats?.sources?.providers || []);

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
          return (
            <button
              key={item.id}
              type="button"
              className={view === item.id ? "active" : ""}
              onClick={() => onChange(item.id)}
            >
              <Icon size={18} />
              <span>{item.label}</span>
            </button>
          );
        })}
      </nav>

      <div className="sidebar-summary">
        <span className="summary-label">本地资料</span>
        <strong>{formatNumber(stats?.media?.total)} 部作品</strong>
        <span>{formatNumber(stats?.sources?.total)} 条来源</span>
        <div className="summary-grid">
          {providers.map((provider) => <Fragment key={provider.id}><span>{provider.label || provider.id}</span><b>{formatNumber(provider.count)}</b></Fragment>)}
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
