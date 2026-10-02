import { FolderInput, RefreshCw, Search, X } from "lucide-react";


const TITLES = {
  dashboard: "仪表盘",
  library: "影视库",
  resources: "资源库",
  pending: "待匹配",
  duplicates: "多来源作品",
  imports: "入库记录",
  settings: "设置",
};


export default function Topbar({
  view,
  search,
  onSearch,
  onSubmitSearch,
  searchRef,
  syncing,
  onSync,
  onManualImport,
}) {
  const dataView = ["library", "resources", "pending", "duplicates"].includes(view);
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

      {view === "imports" ? (
        <button type="button" className="icon-command" onClick={onManualImport} title="手动入库">
          <FolderInput size={17} />
          <span>手动入库</span>
        </button>
      ) : dataView && view !== "library" ? (
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
