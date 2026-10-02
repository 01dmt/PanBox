import { Archive, ChevronRight, Database, FileText, UserRound } from "lucide-react";
import { displayResourceKind, displayStatus, formatBytes, formatNumber } from "../lib/format";

function kindIcon(kind) {
  if (kind === "person") return <UserRound size={18} />;
  if (kind === "series") return <Archive size={18} />;
  return <Database size={18} />;
}

export default function ResourceLibrary({ items, loading, selectedId, onSelect, total, page, pages, onPageChange }) {
  return <section className="resource-library" aria-label="资源库">
    <header className="resource-library-head">
      <div><h1>资源库</h1><p>管理影视、人物、系列和无法由 TMDB 识别的资源包</p></div>
      <div className="resource-library-count"><strong>{formatNumber(total)}</strong><span>个资源条目</span></div>
    </header>
    <div className="resource-library-guide"><FileText size={17} /><span>每个条目保留原始消息和来源，识别失败时可从详情继续手动刮削。</span></div>
    {loading ? <div className="resource-list-loading">正在加载资源…</div> : null}
    {!loading && !items.length ? <div className="resource-empty"><Archive size={30} /><strong>还没有资源条目</strong><span>接收到频道消息后，资源会按条目自动归档。</span></div> : null}
    {!loading && items.length ? <div className="resource-list">{items.map((item) => {
      const metadata = item.resource_metadata_json ? (() => { try { return JSON.parse(item.resource_metadata_json); } catch { return {}; } })() : {};
      const fields = metadata.channel_fields || {};
      const title = item.tmdb_title || item.title || "未命名资源";
      const sourceCount = item.source_count || item.matched_source_count || 0;
      const subtitle = [item.year || "年份未知", displayResourceKind(item.resource_kind), sourceCount ? `${formatNumber(sourceCount)} 个来源` : "暂无来源"].join(" · ");
      return <button type="button" className={`resource-row ${selectedId === item.id ? "selected" : ""}`} key={item.id} onClick={() => onSelect(item.id)}>
        <span className="resource-row-icon">{kindIcon(item.resource_kind)}</span>
        <span className="resource-row-main"><strong>{title}</strong><small>{subtitle}</small>{fields.overview ? <em>{String(fields.overview).slice(0, 100)}</em> : null}</span>
        <span className="resource-row-facts"><span>{item.file_count ? `${formatNumber(item.file_count)} 个文件` : sourceCount ? `${formatNumber(sourceCount)} 个来源` : "待补充"}</span><span>{item.total_size ? formatBytes(item.total_size) : "—"}</span></span>
        <span className={`resource-row-status resource-row-status-${item.tmdb_status || "pending"}`}>{item.resource_kind === "media" ? displayStatus(item.tmdb_status) : "待刮削"}</span>
        <ChevronRight size={17} className="resource-row-arrow" />
      </button>;
    })}</div> : null}
    {pages > 1 ? <footer className="resource-library-footer"><button type="button" disabled={page <= 1} onClick={() => onPageChange(page - 1)}>上一页</button><span>第 {page} / {pages} 页</span><button type="button" disabled={page >= pages} onClick={() => onPageChange(page + 1)}>下一页</button></footer> : null}
  </section>;
}
