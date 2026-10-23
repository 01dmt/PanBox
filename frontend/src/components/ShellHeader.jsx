import {
  CircleAlert,
  FileClock,
  Film,
  FolderInput,
  FolderOpen,
  Layers3,
  LayoutDashboard,
  Moon,
  RefreshCw,
  Settings,
  Sun,
  Tv,
} from "lucide-react";
import { formatNumber } from "../lib/format";

const NAV_ITEMS = [
  { id: "movies", label: "电影", icon: Film },
  { id: "tv", label: "剧集", icon: Tv },
  { id: "resources", label: "资源库", icon: Layers3 },
  { id: "series", label: "系列", icon: FolderOpen },
  { id: "pending", label: "待匹配", icon: CircleAlert, badgeKey: "pending" },
  { id: "imports", label: "入库记录", icon: FileClock },
  { id: "dashboard", label: "仪表盘", icon: LayoutDashboard },
  { id: "settings", label: "设置", icon: Settings },
];

export default function ShellHeader({
  view,
  onChange,
  stats,
  config,
  theme,
  onToggleTheme,
  syncing,
  onSync,
  onManualImport,
}) {
  const pendingCount = stats?.media?.pending ?? 0;

  return (
    <header className="shell-header" aria-label="全局导航">
      <div className="shell-header-inner">
        <div className="shell-brand" onClick={() => onChange("movies")} role="button" tabIndex={0} onKeyDown={(e) => e.key === "Enter" && onChange("movies")}>
          <span className="shell-brand-mark">
            <Film size={22} strokeWidth={2.2} />
          </span>
          <div className="shell-brand-text">
            <span className="shell-brand-name">PanBox</span>
            <span className="shell-brand-tagline">影视资料与分享社区</span>
          </div>
        </div>

        <nav className="shell-nav" aria-label="主导航">
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            const isActive = view === item.id || (item.id === "movies" && view === "library");
            const badge = item.badgeKey === "pending" && pendingCount > 0 ? pendingCount : null;
            return (
              <button
                key={item.id}
                type="button"
                className={`shell-nav-link ${isActive ? "active" : ""}`}
                onClick={() => onChange(item.id)}
                aria-current={isActive ? "page" : undefined}
              >
                <Icon size={16} aria-hidden="true" />
                <span>{item.label}</span>
                {badge ? <b className="shell-nav-badge">{badge > 99 ? "99+" : badge}</b> : null}
              </button>
            );
          })}
        </nav>

        <div className="shell-actions">
          {view === "imports" && (
            <button
              type="button"
              className="shell-action-btn"
              onClick={onManualImport}
              title="手动入库"
            >
              <FolderInput size={16} />
              <span className="hide-sm">手动入库</span>
            </button>
          )}

          {["pending", "library", "movies", "tv", "resources"].includes(view) && (
            <button
              type="button"
              className="shell-action-btn"
              onClick={onSync}
              disabled={syncing}
              title="自动匹配下一批"
            >
              <RefreshCw size={15} className={syncing ? "spin" : ""} />
              <span className="hide-sm">{syncing ? "匹配中" : "自动匹配"}</span>
            </button>
          )}

          <div
            className={`shell-tmdb-pill ${config?.tmdb_configured ? "connected" : "offline"}`}
            title={config?.tmdb_auth_mode ? `TMDB: ${config.tmdb_auth_mode}` : "未配置 TMDB"}
          >
            <i aria-hidden="true" />
            <span className="hide-sm">{config?.tmdb_configured ? "TMDB" : "未配置"}</span>
          </div>

          <button
            type="button"
            className="shell-theme-toggle"
            onClick={onToggleTheme}
            aria-label={theme === "dark" ? "切换到浅色模式" : "切换到暗色模式"}
            title={theme === "dark" ? "浅色模式" : "暗色模式"}
          >
            {theme === "dark" ? <Sun size={17} /> : <Moon size={17} />}
          </button>
        </div>
      </div>
    </header>
  );
}
