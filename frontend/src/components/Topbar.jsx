import { CheckCircle2, CloudOff, RefreshCw, Search, X } from "lucide-react";


const TITLES = {
  dashboard: "仪表盘",
  library: "资料库",
  pending: "待匹配",
  duplicates: "多来源作品",
  imports: "导入记录",
  settings: "设置",
};


export default function Topbar({
  view,
  search,
  onSearch,
  onSubmitSearch,
  searchRef,
  config,
  syncing,
  onSync,
}) {
  const dataView = ["library", "pending", "duplicates"].includes(view);
  return (
    <header className={`topbar ${view === "library" ? "topbar--library" : ""}`}>
      <div className="mobile-title">{TITLES[view]}</div>
      {dataView && view !== "library" ? (
        <form className="global-search" role="search" aria-label="搜索缓存资源" onSubmit={(event) => {
          event.preventDefault();
          onSubmitSearch();
        }}>
          <input
            ref={searchRef}
            value={search}
            onChange={(event) => onSearch(event.target.value)}
            placeholder="片名、年份、TMDB ID、缓存文件名"
            aria-label="搜索资料库"
          />
          <button type="button" className="search-clear" onClick={() => onSearch("")} disabled={!search} title="清空搜索" aria-label="清空搜索">
            <X size={16} aria-hidden="true" />
          </button>
          <button type="submit" className="search-submit" title="搜索缓存资源" aria-label="搜索缓存资源">
            <Search size={18} aria-hidden="true" />
          </button>
        </form>
      ) : view === "library" ? null : (
        <h1>{TITLES[view]}</h1>
      )}

      <div className={`tmdb-state ${config?.tmdb_configured ? "connected" : "offline"}`}>
        {config?.tmdb_configured ? <CheckCircle2 size={17} /> : <CloudOff size={17} />}
        <div>
          <strong>{config?.tmdb_configured ? "TMDB 已连接" : "TMDB 未配置"}</strong>
          <span>{config?.tmdb_auth_mode === "bearer" ? "Bearer Token" : config?.tmdb_auth_mode === "api_key" ? "API Key" : "仅本地管理"}</span>
        </div>
      </div>

      {dataView ? (
        <button
          type="button"
          className="icon-command"
          onClick={onSync}
          disabled={syncing}
          title="自动匹配下一批"
        >
          <RefreshCw size={18} className={syncing ? "spin" : ""} />
          <span>{syncing ? "匹配中" : "匹配 20 条"}</span>
        </button>
      ) : null}
    </header>
  );
}
