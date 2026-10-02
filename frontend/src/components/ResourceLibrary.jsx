import { Archive, ChevronRight, Database, FileText, UserRound, X, ExternalLink } from "lucide-react";
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

export function ResourceDetail({ item, loading, onClose }) {
  if (loading || !item) return <aside className="resource-detail"><span>正在加载资源详情…</span></aside>;
  const metadata = item.resource_metadata_json ? (() => { try { return JSON.parse(item.resource_metadata_json); } catch { return {}; } })() : {};
  const fields = metadata.channel_fields || {};
  return <aside className="resource-detail">
    <header><div><small>资源条目</small><h2>{item.tmdb_title || item.title || "未命名资源"}</h2></div><button type="button" className="icon-only" onClick={onClose} aria-label="关闭详情"><X size={18} /></button></header>
    <div className="resource-detail-summary"><span>{displayResourceKind(item.resource_kind)}</span><span>{item.year || "年份未知"}</span><span>{item.source_count || 0} 个来源</span></div>
    <section><h3>刮削字段</h3>{Object.entries(fields).length ? <dl>{Object.entries(fields).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{String(value)}</dd></div>)}</dl> : <p className="muted">暂无结构化字段，可根据原始消息手动补充。</p>}</section>
    <section><h3>来源</h3><div className="resource-detail-sources">{(item.sources || []).map((source) => <div key={source.id}><strong>{source.provider || source.source_type}</strong><span>{source.raw_label || source.filename || "来源链接"}</span><a href={source.url} target="_blank" rel="noreferrer"><ExternalLink size={14} /></a></div>)}</div></section>
    <section><h3>原始消息 / 来源原文</h3>{(item.original_messages || []).length ? item.original_messages.map((message) => <article className="resource-raw-message" key={message.id}><div><strong>{message.channel_name || "未知频道"}</strong><small>{message.message_url || message.event_id}</small></div><pre>{message.raw_text || ""}</pre></article>) : (item.sources || []).length ? <div className="resource-detail-sources">{item.sources.map((source) => <article className="resource-raw-message" key={source.id}><div><strong>{source.provider || source.source_type} · 来源记录</strong><small>{source.url}</small></div><pre>{source.raw_text || source.raw_label || "没有保存来源原文"}</pre></article>)}</div> : <p className="muted">没有保存原始消息或来源原文。</p>}</section>
  </aside>;
}
