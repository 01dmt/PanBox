import { useEffect, useState } from "react";
import {
  Archive, Check, ChevronDown, Clipboard, Cloud, ExternalLink, File, FileText, Folder, LoaderCircle,
  Music2, RefreshCw, UserRound, X,
} from "lucide-react";
import { formatBytes, formatDateTime, formatNumber, posterUrl } from "../lib/format";

const CLASS_LABELS = { media: "影视", collection: "资源包", music: "音乐", person: "人物", other: "其他" };
const TMDB_LABELS = { matched: "TMDB 已匹配", review: "待确认", not_found: "未找到", error: "匹配错误", unrecognized: "未识别" };
const AVAILABILITY_LABELS = { available: "可用", pending: "待检查", invalid: "失效", unknown: "未知" };

function classIcon(value) {
  if (value === "collection") return <Folder size={18} />;
  if (value === "music") return <Music2 size={18} />;
  if (value === "person") return <UserRound size={18} />;
  if (value === "other") return <File size={18} />;
  return <Archive size={18} />;
}

function ProviderMark({ provider }) {
  return <span className="resource-provider" style={{ "--provider-color": provider?.color || "#7b8791" }}>{provider?.icon === "file" ? <File size={13} /> : <Cloud size={13} />}<b>{provider?.label || "未知来源"}</b></span>;
}

function StatusDot({ status }) {
  return <span className={`resource-availability resource-availability-${status || "unknown"}`}><i aria-hidden="true" />{AVAILABILITY_LABELS[status] || "未知"}</span>;
}

function QuickStat({ label, value, tone }) {
  return <div className={`resource-stat resource-stat-${tone}`}><span>{label}</span><strong>{formatNumber(value)}</strong></div>;
}

function RowActions({ item, onAction, onCheck, checking }) {
  return <div className="resource-row-actions">
    <button type="button" className="icon-only" title="复制分享链接" aria-label="复制分享链接" onClick={(event) => { event.stopPropagation(); onAction(item.id, "copy"); }}><Clipboard size={15} /></button>
    <button type="button" className="icon-only" title="打开分享" aria-label="打开分享" onClick={(event) => { event.stopPropagation(); onAction(item.id, "open"); }}><ExternalLink size={15} /></button>
    <button type="button" className="icon-only" title="重新检查" aria-label="重新检查" disabled={checking || !item.provider?.can_check} onClick={(event) => { event.stopPropagation(); onCheck(item.id); }}><RefreshCw size={15} className={checking ? "spin" : ""} /></button>
  </div>;
}

function ResourceRow({ item, selectedId, onSelect, onAction, onCheck, checkingId, compact = false }) {
  const summaryData = item.summary || {};
  const title = item.title || "未命名资源";
  return <button type="button" className={`resource-row ${compact ? "resource-row-compact" : ""} ${selectedId === item.id ? "selected" : ""}`} key={item.id} onClick={() => onSelect(item.id)} role="listitem">
    <span className={`resource-row-icon resource-row-icon-${item.resource_class || "other"}`}>{item.poster_path ? <img src={posterUrl(item.poster_path, "w92")} alt="" loading="lazy" /> : classIcon(item.resource_class)}</span>
    <span className="resource-row-main"><strong title={title}>{title}</strong><small>{[summaryData.total_size ? formatBytes(summaryData.total_size) : "", item.quality].filter(Boolean).join(" · ")}</small><span className="resource-row-tags"><em>{CLASS_LABELS[item.resource_class] || "其他"}</em>{(item.tags || []).slice(0, 2).map((tag) => <em key={tag}>{tag}</em>)}</span></span>
    <span className="resource-row-provider"><ProviderMark provider={item.provider} /></span>
    <span className="resource-row-status"><StatusDot status={item.availability_status} /><em className={`resource-tmdb resource-tmdb-${item.tmdb_status}`}>{TMDB_LABELS[item.tmdb_status] || "未识别"}</em></span>
    <span className="resource-row-date">{formatDateTime(item.last_checked_at || item.updated_at)}</span>
    <RowActions item={item} onAction={onAction} onCheck={onCheck} checking={checkingId === item.id} />
  </button>;
}

function PersonGroup({ group, selectedId, onSelect, onAction, onCheck, checkingId, expanded, onToggle }) {
  const availability = group.availability_counts || {};
  return <article className="resource-person-group" aria-label={`${group.title}，${group.resource_count} 条资源`}>
    <button type="button" className="resource-person-group-head" aria-expanded={expanded} onClick={onToggle}>
      <span className="resource-row-icon resource-row-icon-person"><UserRound size={19} /></span>
      <span className="resource-person-group-title"><strong>{group.title}</strong><small>{formatNumber(group.resource_count)} 条相关资源 · {formatNumber(availability.available || 0)} 个可用来源</small><span className="resource-row-tags">{(group.tags || []).slice(0, 4).map((tag) => <em key={tag}>{tag}</em>)}</span></span>
      <span className="resource-person-group-providers">{(group.providers || []).map((provider) => <span key={provider.id}>{provider.label} {provider.count}</span>)}</span>
      <ChevronDown size={18} className={expanded ? "resource-person-group-chevron expanded" : "resource-person-group-chevron"} aria-hidden="true" />
    </button>
    {expanded ? <div className="resource-person-resources" role="list" aria-label={`${group.title} 的资源`}>
      {group.resources.map((item) => <ResourceRow key={item.id} item={item} compact selectedId={selectedId} onSelect={onSelect} onAction={onAction} onCheck={onCheck} checkingId={checkingId} />)}
    </div> : null}
  </article>;
}

export default function ResourceLibrary({ items, loading, grouped = false, selectedId, onSelect, total, page, pages, onPageChange, summary, onAction, onCheck, checkingId, onClearFilters }) {
  const [expandedGroups, setExpandedGroups] = useState(new Set());
  useEffect(() => {
    if (!grouped) { setExpandedGroups(new Set()); return; }
    setExpandedGroups(new Set(items.slice(0, 1).map((item) => item.id)));
  }, [grouped, items]);
  const toggleGroup = (id) => setExpandedGroups((current) => {
    const next = new Set(current);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  return <section className="resource-library" aria-label="资源库">
    <header className="resource-library-head"><div><h1>资源库</h1><p>{grouped ? "按人物聚合显示，展开即可查看旗下所有资源来源。" : "跨网盘检索与管理，每个分享来源独立保留。"}</p></div><div className="resource-library-count"><strong>{formatNumber(total)}</strong><span>{grouped ? "位人物" : "条来源"}</span></div></header>
    <div className="resource-summary" aria-label="资源状态概览"><QuickStat label="资源总数" value={summary?.total ?? total} tone="total" /><QuickStat label="可用来源" value={summary?.available} tone="available" /><QuickStat label="待检查" value={summary?.pending} tone="pending" /><QuickStat label="失效来源" value={summary?.invalid} tone="invalid" /></div>
    <div className="resource-library-guide"><FileText size={16} /><span>标题、文件名和原始消息都可以检索。</span></div>
    {loading ? <div className="resource-list-loading"><LoaderCircle size={20} className="spin" />正在加载来源…</div> : null}
    {!loading && !items.length ? <div className="resource-empty"><Archive size={30} /><strong>没有符合条件的来源</strong><span>调整筛选条件，或等待新的频道消息入库。</span><button type="button" className="button small" onClick={onClearFilters}>清除筛选</button></div> : null}
    {!loading && items.length && grouped ? <div className="resource-person-list" role="list" aria-label="人物列表">{items.map((group) => <PersonGroup key={group.id} group={group} expanded={expandedGroups.has(group.id)} onToggle={() => toggleGroup(group.id)} selectedId={selectedId} onSelect={onSelect} onAction={onAction} onCheck={onCheck} checkingId={checkingId} />)}</div> : null}
    {!loading && items.length && !grouped ? <div className="resource-list" role="list" aria-label="来源列表">{items.map((item) => <ResourceRow key={item.id} item={item} selectedId={selectedId} onSelect={onSelect} onAction={onAction} onCheck={onCheck} checkingId={checkingId} />)}</div> : null}
    {pages > 1 ? <footer className="resource-library-footer"><button type="button" disabled={page <= 1} onClick={() => onPageChange(page - 1)}>上一页</button><span>第 {page} / {pages} 页 · 共 {formatNumber(total)} 条</span><button type="button" disabled={page >= pages} onClick={() => onPageChange(page + 1)}>下一页</button></footer> : null}
  </section>;
}

function DetailValue({ label, children }) { return <div className="resource-detail-fact"><dt>{label}</dt><dd>{children || "—"}</dd></div>; }

function CopyButton({ value, label = "复制" }) {
  const [copied, setCopied] = useState(false);
  if (!value) return null;
  const copy = async () => { await navigator.clipboard.writeText(value); setCopied(true); window.setTimeout(() => setCopied(false), 1400); };
  return <button type="button" className="button small ghost" onClick={copy}>{copied ? <Check size={14} /> : <Clipboard size={14} />}{copied ? "已复制" : label}</button>;
}

export function ResourceDetail({ item, loading, onClose, onAction, onCheck, checking, onUpdate, onViewMedia, mobileOpen = false }) {
  const [editing, setEditing] = useState(false);
  const [tags, setTags] = useState([]);
  const [notes, setNotes] = useState("");
  const [resourceClass, setResourceClass] = useState("media");
  useEffect(() => { setTags(item?.tags || []); setNotes(item?.notes || ""); setResourceClass(item?.resource_class || "media"); setEditing(false); }, [item?.id]);
  const summary = item?.summary || {};
  if (loading || !item) return <aside className="resource-detail"><span className="resource-detail-loading"><LoaderCircle size={20} className="spin" />正在加载资源详情…</span></aside>;
  const accessCode = item.access_code_masked ? item.url?.match(/(?:password|pwd|code)=([^&#]+)/i)?.[1] : "";
  return <aside className={`resource-detail ${mobileOpen ? "mobile-open" : ""}`} aria-label="来源详情">
    <header><div><small>来源详情</small><h2>{item.title || "未命名资源"}</h2><span className="resource-detail-subtitle">{item.person_name ? `人物：${item.person_name} · ` : ""}{CLASS_LABELS[item.resource_class] || "其他"} · {item.tmdb_status === "matched" ? `TMDB #${item.tmdb_id}` : TMDB_LABELS[item.tmdb_status] || "未识别"}{item.tmdb_status === "matched" && item.media_id && onViewMedia ? <button type="button" className="resource-detail-media-link" onClick={() => onViewMedia(item.media_id)}>在影视库查看</button> : null}</span></div><button type="button" className="icon-only" onClick={onClose} aria-label="关闭详情"><X size={18} /></button></header>
    <div className="resource-detail-actions"><button type="button" className="button primary" onClick={() => onAction(item.id, "copy")}><Clipboard size={15} />复制链接</button><button type="button" className="button ghost" onClick={() => onAction(item.id, "open")}><ExternalLink size={15} />打开分享</button><button type="button" className="button ghost" disabled={checking || !item.provider?.can_check} onClick={() => onCheck(item.id)}><RefreshCw size={15} className={checking ? "spin" : ""} />重新检查</button></div>
    <section className="resource-detail-section resource-detail-links"><h3>分享信息</h3><div className="resource-detail-link"><span>分享链接</span><code>{item.url_masked || "—"}</code><CopyButton value={item.url} /></div><div className="resource-detail-link"><span>访问码</span><code>{item.access_code_masked || "无"}</code><CopyButton value={accessCode} label="复制访问码" /></div></section>
    <section className="resource-detail-section"><h3>状态与分类 <button type="button" className="text-command" onClick={() => setEditing((value) => !value)}>{editing ? "取消" : "编辑"}</button></h3>{editing ? <form className="resource-edit-form" onSubmit={(event) => { event.preventDefault(); onUpdate({ resource_class: resourceClass, tags, notes }); setEditing(false); }}><label>资源类型<select value={resourceClass} onChange={(event) => setResourceClass(event.target.value)}>{Object.entries(CLASS_LABELS).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label><label>标签<input value={tags.join(" ")} onChange={(event) => setTags(event.target.value.split(/[\s,，]+/).filter(Boolean))} placeholder="周星驰 2022 4K" /></label><label>备注<textarea value={notes} onChange={(event) => setNotes(event.target.value)} rows="3" /></label><button type="submit" className="button primary small">保存</button></form> : <><div className="resource-detail-badges"><ProviderMark provider={item.provider} /><StatusDot status={item.availability_status} /><em className={`resource-tmdb resource-tmdb-${item.tmdb_status}`}>{TMDB_LABELS[item.tmdb_status] || "未识别"}</em></div><dl className="resource-detail-facts"><DetailValue label="网盘来源"><ProviderMark provider={item.provider} /></DetailValue><DetailValue label="来源状态"><StatusDot status={item.availability_status} /></DetailValue><DetailValue label="检查时间">{formatDateTime(item.last_checked_at)}</DetailValue><DetailValue label="资源大小">{formatBytes(summary.total_size)}</DetailValue>{(summary.quality_tags || []).length ? <DetailValue label="技术标签">{(summary.quality_tags || []).join(" · ")}</DetailValue> : null}<DetailValue label="来源频道">{item.channel}</DetailValue></dl>{item.last_check_error ? <p className="resource-detail-error">{item.last_check_error}</p> : null}</>}</section>
    <section className="resource-detail-section"><details><summary>原始消息</summary>{item.original_messages?.length ? item.original_messages.map((message) => <article className="resource-raw-message" key={message.id}><div><strong>{message.channel_name || "未知频道"}</strong><small>{message.message_url || message.received_at}</small></div><pre>{message.raw_text || ""}</pre></article>) : <pre className="resource-raw-text">{item.raw_text || "没有保存原始消息。"}</pre>}</details></section>
    {item.related_sources?.length ? <section className="resource-detail-section"><details><summary>相关来源（{item.related_sources.length}）</summary><div className="resource-related-list">{item.related_sources.map((source) => <button type="button" key={source.id} onClick={() => onAction(source.id, "select")}><ProviderMark provider={source.provider} /><span>{source.title}</span><StatusDot status={source.availability_status} /></button>)}</div></details></section> : null}
  </aside>;
}
