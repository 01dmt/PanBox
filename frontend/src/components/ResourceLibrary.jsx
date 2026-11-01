import { useEffect, useMemo, useState } from "react";
import {
  Archive,
  ArrowLeft,
  Check,
  ChevronRight,
  Clipboard,
  Cloud,
  Disc,
  ExternalLink,
  File,
  FileText,
  Film,
  Folder,
  HardDrive,
  Link,
  LoaderCircle,
  Monitor,
  Music2,
  RefreshCw,
  Subtitles,
  UserRound,
  X,
} from "lucide-react";
import { formatBytes, formatDate, formatDateTime, formatNumber, posterUrl } from "../lib/format";

const CLASS_LABELS = { media: "影视", collection: "合集", music: "音乐", person: "人物", other: "其他" };
const TMDB_LABELS = { matched: "TMDB 已匹配", review: "待确认", not_found: "未找到", error: "匹配错误", unrecognized: "未识别" };
const AVAILABILITY_LABELS = { available: "可用", pending: "待检查", invalid: "失效", unknown: "未知" };

const PROVIDER_LOGOS = {
  "115": "/providers/115.png",
  "123": "/providers/123.png",
  guangya: "/providers/guangya.png",
};

function classIcon(value) {
  if (value === "collection") return <Folder size={18} />;
  if (value === "music") return <Music2 size={18} />;
  if (value === "person") return <UserRound size={18} />;
  if (value === "other") return <File size={18} />;
  return <Film size={18} />;
}

function ProviderMark({ provider, compact = false }) {
  const logo = PROVIDER_LOGOS[provider?.id];
  return (
    <span className={`resource-provider-badge ${compact ? "compact" : ""}`} style={{ "--provider-color": provider?.color || "#7b8791" }}>
      {logo ? (
        <img className="resource-provider-logo" src={logo} alt="" width={14} height={14} />
      ) : provider?.icon === "file" ? (
        <Link size={13} />
      ) : (
        <Cloud size={13} />
      )}
      <b>{provider?.label || "未知来源"}</b>
    </span>
  );
}

function StatusDot({ status }) {
  return (
    <span className={`resource-availability resource-availability-${status || "unknown"}`}>
      <i aria-hidden="true" />
      <span>{AVAILABILITY_LABELS[status] || "未知"}</span>
    </span>
  );
}

function extractResourceFacts(item) {
  const summary = item.summary || {};
  const qualityTags = Array.isArray(summary.quality_tags) ? summary.quality_tags : [];
  const facts = [];

  // 1. Resolution / Quality
  const rawQuality = item.quality || qualityTags.find((t) => /2160p|4k|1080p|1080i|720p|480p/i.test(t));
  if (rawQuality) {
    const qUpper = String(rawQuality).toUpperCase();
    facts.push({
      type: "quality",
      label: qUpper.replace("2160P", "4K"),
      Icon: Monitor,
    });
  }

  // 2. Audio & Subtitles
  const audioSubParts = [];
  const audioSubTag = qualityTags.find((t) => /中字|双语|内封|简中|繁中|国配|国语|粤语|dtsx|atmos|ddp|flac/i.test(t));
  if (audioSubTag) audioSubParts.push(audioSubTag);
  if (audioSubParts.length > 0) {
    facts.push({
      type: "audio_sub",
      label: audioSubParts.slice(0, 1).join(" · "),
      Icon: Subtitles,
    });
  }

  // 3. Source Version & Codec (片源/压制)
  const versionParts = [];
  const verTag = qualityTags.find((t) => /remux|bluray|web-dl|bdrip|uhd|hdr|dv|dolby|h\.265|hevc|h\.264|av1/i.test(t));
  if (verTag) versionParts.push(verTag.toUpperCase());
  if (versionParts.length > 0) {
    facts.push({
      type: "version",
      label: versionParts.slice(0, 1).join(" · "),
      Icon: Disc,
    });
  }

  return facts;
}

/* ==========================================================================
   High-Density Table Row Component (高信息密度行)
   ========================================================================== */
function ResourceTableRow({
  item,
  selectedId,
  onSelect,
  onAction,
  onCheck,
  checkingId,
  onCopyNotice,
}) {
  const title = item.title || item.filename || "未命名资源";
  const facts = extractResourceFacts(item);
  const isSelected = selectedId === item.id;
  const isChecking = checkingId === item.id;
  const summary = item.summary || {};

  const handleCopyCode = (e) => {
    e.stopPropagation();
    if (item.access_code) {
      navigator.clipboard.writeText(item.access_code);
      onCopyNotice?.("提取码已复制");
    }
  };

  const handleCopyLink = (e) => {
    e.stopPropagation();
    onAction(item.id, "copy");
  };

  const handleOpenLink = (e) => {
    e.stopPropagation();
    onAction(item.id, "open");
  };

  const handleRecheck = (e) => {
    e.stopPropagation();
    onCheck(item.id);
  };

  return (
    <div
      className={`resource-table-row ${isSelected ? "selected" : ""}`}
      onClick={() => onSelect(item.id)}
      role="row"
      tabIndex={0}
      onKeyDown={(e) => e.key === "Enter" && onSelect(item.id)}
      aria-label={`查看 ${title} 详情`}
    >
      {/* 1. Main: Thumbnail + Title + TMDB relation subline */}
      <div className="table-col col-main">
        <div className="table-thumb">
          {item.poster_path ? (
            <img src={posterUrl(item.poster_path, "w92")} alt="" loading="lazy" />
          ) : (
            <div className={`table-icon-box ${item.resource_class || "other"}`}>
              {classIcon(item.resource_class)}
            </div>
          )}
        </div>
        <div className="table-title-meta">
          <strong className="table-title" title={title}>
            {title}
          </strong>
          {item.tmdb_title ? (
            <span className="table-subline" title={`关联影视: ${item.tmdb_title} (${item.year || "未知"})`}>
              关联: {item.tmdb_title} {item.year ? `(${item.year})` : ""}
            </span>
          ) : item.person_name ? (
            <span className="table-subline">人物: {item.person_name}</span>
          ) : null}
        </div>
      </div>

      {/* 2. Provider Badge */}
      <div className="table-col col-provider">
        <ProviderMark provider={item.provider} compact />
      </div>

      {/* 3. Specifications & Facts Pills */}
      <div className="table-col col-facts">
        {facts.map((fact, idx) => (
          <span key={idx} className={`dense-fact-tag ${fact.type}`} title={fact.label}>
            {fact.label}
          </span>
        ))}
        {facts.length === 0 && <span className="table-empty-dash">—</span>}
      </div>

      {/* 4. File Size */}
      <div className="table-col col-size">
        {summary.total_size ? formatBytes(summary.total_size) : "—"}
      </div>

      {/* 5. Ingestion Channel */}
      <div className="table-col col-channel" title={item.channel || "未知频道"}>
        <span>{item.channel || "未知频道"}</span>
      </div>

      {/* 6. Status */}
      <div className="table-col col-status">
        <StatusDot status={item.availability_status} />
        {item.tmdb_status && item.tmdb_status !== "matched" && (
          <span className={`dense-tmdb-tag ${item.tmdb_status}`}>
            {TMDB_LABELS[item.tmdb_status] || "未识别"}
          </span>
        )}
      </div>

      {/* 7. Date */}
      <div className="table-col col-date" title={item.last_checked_at || item.updated_at}>
        <span>{formatDate(item.last_checked_at || item.updated_at || item.created_at)}</span>
      </div>

      {/* 8. Fast Actions */}
      <div className="table-col col-actions" onClick={(e) => e.stopPropagation()}>
        <button
          type="button"
          className="table-action-icon"
          title="复制分享链接"
          aria-label="复制分享链接"
          onClick={handleCopyLink}
        >
          <Clipboard size={14} />
        </button>
        <button
          type="button"
          className="table-action-icon"
          title="在浏览器中直接打开网盘分享"
          aria-label="打开分享"
          onClick={handleOpenLink}
        >
          <ExternalLink size={14} />
        </button>
        <button
          type="button"
          className="table-action-icon"
          title="单条重新检测链接可用性"
          aria-label="检查有效性"
          disabled={isChecking || !item.provider?.can_check}
          onClick={handleRecheck}
        >
          <RefreshCw size={14} className={isChecking ? "spin" : ""} />
        </button>
        <button
          type="button"
          className="table-action-icon details-btn"
          title="查看完整详情"
          aria-label="查看详情"
          onClick={() => onSelect(item.id)}
        >
          <ChevronRight size={14} />
        </button>
      </div>
    </div>
  );
}

/* ==========================================================================
   High-Density Table View Wrapper
   ========================================================================== */
function ResourceTableView({
  items,
  selectedId,
  onSelect,
  onAction,
  onCheck,
  checkingId,
  onCopyNotice,
}) {
  return (
    <div className="resource-table-container" role="table" aria-label="资源清单表格">
      <div className="resource-table-head" role="row">
        <div className="table-col col-main" role="columnheader">资源名称 / 关联影视</div>
        <div className="table-col col-provider" role="columnheader">网盘</div>
        <div className="table-col col-facts" role="columnheader">规格标签</div>
        <div className="table-col col-size" role="columnheader">体积</div>
        <div className="table-col col-channel" role="columnheader">来源频道</div>
        <div className="table-col col-status" role="columnheader">状态</div>
        <div className="table-col col-date" role="columnheader">时间</div>
        <div className="table-col col-actions" role="columnheader">操作</div>
      </div>
      <div className="resource-table-body" role="rowgroup">
        {items.map((item) => (
          <ResourceTableRow
            key={item.id}
            item={item}
            selectedId={selectedId}
            onSelect={onSelect}
            onAction={onAction}
            onCheck={onCheck}
            checkingId={checkingId}
            onCopyNotice={onCopyNotice}
          />
        ))}
      </div>
    </div>
  );
}

/* ==========================================================================
   Optional Asset Cards (Grid View)
   ========================================================================== */
function ResourceAssetCard({
  item,
  selectedId,
  onSelect,
  onAction,
  onCheck,
  checkingId,
}) {
  const title = item.title || item.filename || "未命名资源";
  const facts = extractResourceFacts(item);
  const isSelected = selectedId === item.id;
  const isChecking = checkingId === item.id;

  return (
    <article
      className={`resource-asset-card ${isSelected ? "selected" : ""}`}
      onClick={() => onSelect(item.id)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => e.key === "Enter" && onSelect(item.id)}
      aria-label={`查看 ${title} 详情`}
    >
      <div className="resource-card-top">
        <div className="resource-card-provider">
          <ProviderMark provider={item.provider} />
          {item.channel && (
            <span className="resource-card-channel" title={item.channel}>
              {item.channel}
            </span>
          )}
        </div>
        <div className="resource-card-top-badges">
          <StatusDot status={item.availability_status} />
        </div>
      </div>

      <div className="resource-card-body">
        <div className="resource-card-thumb">
          {item.poster_path ? (
            <img src={posterUrl(item.poster_path, "w185")} alt="" loading="lazy" />
          ) : (
            <div className={`resource-card-icon-box ${item.resource_class || "other"}`}>
              {classIcon(item.resource_class)}
            </div>
          )}
        </div>
        <div className="resource-card-main">
          <h3 className="resource-card-title" title={title}>
            {title}
          </h3>
          {item.tmdb_title && item.tmdb_title !== title && (
            <p className="resource-card-relation" title={item.tmdb_title}>
              关联作品: {item.tmdb_title} {item.year ? `(${item.year})` : ""}
            </p>
          )}
        </div>
      </div>

      <div className="resource-card-dotted-divider" aria-hidden="true" />

      <div className="resource-card-facts">
        {facts.map((fact, idx) => (
          <span key={idx} className={`resource-fact-pill ${fact.type}`} title={fact.label}>
            <span>{fact.label}</span>
          </span>
        ))}
      </div>

      <div className="resource-card-actions" onClick={(e) => e.stopPropagation()}>
        <button type="button" className="resource-card-btn" onClick={() => onAction(item.id, "copy")}>
          <Clipboard size={13} />
          <span>复制</span>
        </button>
        <button type="button" className="resource-card-btn" onClick={() => onAction(item.id, "open")}>
          <ExternalLink size={13} />
          <span>打开</span>
        </button>
        <button
          type="button"
          className="resource-card-btn icon-only"
          disabled={isChecking || !item.provider?.can_check}
          onClick={() => onCheck(item.id)}
        >
          <RefreshCw size={13} className={isChecking ? "spin" : ""} />
        </button>
      </div>
    </article>
  );
}

/* ==========================================================================
   High-Density Person Row Component
   ========================================================================== */
function GroupRow({ group, onOpen, kind = "person" }) {
  const availability = group.availability_counts || {};
  const isSeries = kind === "series";
  const Icon = isSeries ? Folder : UserRound;
  return (
    <div
      className="resource-person-dense-row"
      role="row"
      tabIndex={0}
      onKeyDown={(e) => e.key === "Enter" && onOpen(group.id)}
      aria-label={`查看 ${group.title} 的全部资源`}
      onClick={() => onOpen(group.id)}
    >
      <div className="person-avatar-col">
        <Icon size={16} />
      </div>

      <strong className="person-name-col" title={group.title}>
        {group.title}
      </strong>

      <div className="person-count-col">
        <span>{formatNumber(group.resource_count)} 条{isSeries ? "链接" : "资源"}</span>
        <small>({formatNumber(availability.available || 0)} 可用)</small>
      </div>

      <div className="person-tags-col">
        {(group.tags || []).slice(0, 3).map((tag) => (
          <span key={tag} className="person-tag-pill">
            {tag}
          </span>
        ))}
      </div>

      <div className="person-providers-col">
        {(group.providers || []).map((p) => (
          <span key={p.id} className="person-prov-pill">
            {p.label} {p.count}
          </span>
        ))}
      </div>

      <div className="person-arrow-col">
        <ChevronRight size={16} />
      </div>
    </div>
  );
}

export function PersonResources({
  group,
  loading = false,
  onBack,
  backLabel,
  selectedId,
  onSelect,
  onAction,
  onCheck,
  checkingId,
}) {
  const [page, setPage] = useState(1);
  const availability = group.availability_counts || {};
  const isSeries = group.resource_class === "collection";
  const resolvedBackLabel = backLabel || (isSeries ? "返回合集列表" : "返回人物列表");
  const pageSize = 50;
  const resources = group.resources || [];
  const pages = Math.max(1, Math.ceil(resources.length / pageSize));
  const current = Math.min(Math.max(1, page), pages);
  const visible = resources.slice((current - 1) * pageSize, current * pageSize);

  useEffect(() => {
    setPage(1);
  }, [group.id]);

  return (
    <section className="resource-library-main" aria-label={`${group.title} 的资源`}>
      <div className="resource-person-detail-top">
        <button type="button" className="resource-back-btn" onClick={onBack}>
          <ArrowLeft size={16} />
          <span>{resolvedBackLabel}</span>
        </button>
        <div className="resource-person-head-info">
          <h2>{group.title}</h2>
          <p>
            {formatNumber(group.resource_count)} 条相关资源 · {formatNumber(availability.available || 0)} 个可用来源
            {(group.providers || []).length
              ? ` · ${group.providers.map((p) => `${p.label} ${p.count}`).join(" / ")}`
              : ""}
          </p>
        </div>
      </div>

      {loading && !resources.length ? (
        <div className="resource-list-loading" role="status">
          正在加载该分组的资源…
        </div>
      ) : (
        <ResourceTableView
          items={visible}
          selectedId={selectedId}
          onSelect={onSelect}
          onAction={onAction}
          onCheck={onCheck}
          checkingId={checkingId}
        />
      )}

      {pages > 1 && (
        <footer className="resource-library-pagination">
          <button type="button" disabled={current <= 1} onClick={() => setPage(current - 1)}>
            上一页
          </button>
          <span>
            第 {current} / {pages} 页 · 共 {formatNumber(resources.length)} 条
          </span>
          <button type="button" disabled={current >= pages} onClick={() => setPage(current + 1)}>
            下一页
          </button>
        </footer>
      )}
    </section>
  );
}

/* ==========================================================================
   Main Resource Library Component
   ========================================================================== */
export default function ResourceLibrary({
  items = [],
  loading,
  grouped = "",
  selectedId,
  onSelect,
  total = 0,
  page = 1,
  pages = 1,
  pageSize = 30,
  onPageChange,
  onPageSizeChange,
  onAction,
  onCheck,
  checkingId,
  onClearFilters,
  onOpenGroup,
}) {
  const groupKind = grouped === true ? "person" : grouped;
  const isGrouped = Boolean(groupKind);
  const [layoutMode, setLayoutMode] = useState(() => {
    try {
      return localStorage.getItem("panbox-resource-layout-mode") || "table";
    } catch {
      return "table";
    }
  });

  const [toast, setToast] = useState("");

  const showToast = (msg) => {
    setToast(msg);
    setTimeout(() => setToast(""), 2000);
  };

  const handleLayoutModeChange = (mode) => {
    setLayoutMode(mode);
    try {
      localStorage.setItem("panbox-resource-layout-mode", mode);
    } catch {}
  };

  return (
    <section className="resource-library-main" aria-label="资源库">
      {toast && <div className="resource-toast">{toast}</div>}

      {/* Header bar */}
      <header className="resource-library-header">
        <div className="resource-header-left">
          <h1 className="resource-header-title">资源库</h1>
          <p className="resource-header-desc">
            {groupKind === "person"
              ? "按人物自动归类，一览全部人物创作者及其关联的跨网盘资源。"
              : groupKind === "series"
                ? "按系列/合集自动归类，点开一条合集即可查看它的全部来源链接。"
                : "高信息密度资产清单，一屏纵览各网盘资源、技术规格与健康状态。"}
          </p>
        </div>
        <div className="resource-header-count">
          <strong>{formatNumber(total)}</strong>
          <span>{groupKind === "person" ? "位人物" : groupKind === "series" ? "个合集" : "条来源"}</span>
        </div>
      </header>

      {/* Loading state */}
      {loading && (
        <div className="resource-loading-state">
          <LoaderCircle size={24} className="spin" />
          <span>正在检索网盘资产...</span>
        </div>
      )}

      {/* Empty state */}
      {!loading && !items.length && (
        <div className="resource-empty-state">
          <Archive size={36} />
          <strong>没有找到符合条件的资源来源</strong>
          <p>尝试调整网盘筛选、清空搜索词，或等待频道抓取入库</p>
          <button type="button" className="re0-btn re0-btn-primary small" onClick={onClearFilters}>
            清除全部筛选
          </button>
        </div>
      )}

      {/* Grouped Person / Series Rows */}
      {!loading && items.length > 0 && isGrouped && (
        <div className="resource-person-table" role="table" aria-label={groupKind === "series" ? "合集列表" : "人物列表"}>
          <div className="resource-person-table-head" role="row">
            <span className="col-avatar">{groupKind === "series" ? "合集" : "头像"}</span>
            <span className="col-name">{groupKind === "series" ? "合集名称" : "姓名"}</span>
            <span className="col-count">资源与可用</span>
            <span className="col-tags">标签</span>
            <span className="col-providers">网盘平台分布</span>
            <span className="col-arrow">操作</span>
          </div>
          <div className="resource-person-table-body" role="rowgroup">
            {items.map((group) => (
              <GroupRow key={group.id} group={group} onOpen={onOpenGroup} kind={groupKind} />
            ))}
          </div>
        </div>
      )}

      {/* Standard High-Density Table or Optional Grid Cards */}
      {!loading && items.length > 0 && !isGrouped && (
        layoutMode === "table" ? (
          <ResourceTableView
            items={items}
            selectedId={selectedId}
            onSelect={onSelect}
            onAction={onAction}
            onCheck={onCheck}
            checkingId={checkingId}
            onCopyNotice={showToast}
          />
        ) : (
          <div className="resource-asset-grid" role="list" aria-label="来源资产列表">
            {items.map((item) => (
              <ResourceAssetCard
                key={item.id}
                item={item}
                selectedId={selectedId}
                onSelect={onSelect}
                onAction={onAction}
                onCheck={onCheck}
                checkingId={checkingId}
              />
            ))}
          </div>
        )
      )}

      {/* Pagination Footer */}
      {total > 0 && (
        <footer className="resource-library-pagination">
          <div className="resource-page-size">
            <span>每页</span>
            <select
              value={pageSize}
              onChange={(e) => onPageSizeChange?.(Number(e.target.value))}
              aria-label="每页显示条数"
            >
              <option value={30}>30 条</option>
              <option value={50}>50 条 (推荐)</option>
              <option value={100}>100 条 (高密度)</option>
            </select>
          </div>

          <div className="resource-page-nav">
            <button
              type="button"
              className="resource-page-btn"
              disabled={page <= 1}
              onClick={() => onPageChange(page - 1)}
              aria-label="上一页"
            >
              上一页
            </button>
            <span className="resource-page-indicator">
              第 <b>{page}</b> / {pages} 页
            </span>
            <button
              type="button"
              className="resource-page-btn"
              disabled={page >= pages}
              onClick={() => onPageChange(page + 1)}
              aria-label="下一页"
            >
              下一页
            </button>
          </div>

          <div className="resource-page-total">
            共 {formatNumber(total)} {groupKind === "person" ? "位人物" : groupKind === "series" ? "个合集" : "条来源"}
          </div>
        </footer>
      )}
    </section>
  );
}

function DetailValue({ label, children }) {
  return (
    <div className="resource-detail-fact">
      <dt>{label}</dt>
      <dd>{children || "—"}</dd>
    </div>
  );
}

function CopyButton({ value, label = "复制" }) {
  const [copied, setCopied] = useState(false);
  if (!value) return null;
  const copy = async () => {
    await navigator.clipboard.writeText(value);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1400);
  };
  return (
    <button type="button" className="button small ghost" onClick={copy}>
      {copied ? <Check size={14} /> : <Clipboard size={14} />}
      {copied ? "已复制" : label}
    </button>
  );
}

/* Centered Modal Dialog for Resource Inspection (Clean Default + Click-to-Expand Raw Messages on Right) */
function RawMessageCard({ message, repeat = false }) {
  return (
    <article className={`resource-raw-card ${repeat ? "is-repeat" : ""}`}>
      <div className="resource-raw-card-head">
        <div className="resource-raw-channel">
          <strong>{message.channel_name || "未知频道"}</strong>
          {message.message_url && (
            <a
              href={message.message_url}
              target="_blank"
              rel="noopener noreferrer"
              className="resource-raw-link"
              title="在 Telegram 中查看原消息"
            >
              <ExternalLink size={12} />
            </a>
          )}
        </div>
        <small className="resource-raw-time">
          {message.received_at ? formatDateTime(message.received_at) : ""}
        </small>
      </div>
      <div className="resource-raw-content">
        <pre>{message.raw_text || "无正文内容"}</pre>
      </div>
    </article>
  );
}

export function ResourceDetail({
  item,
  loading,
  onClose,
  onAction,
  onCheck,
  checking,
  onUpdate,
  onViewMedia,
  open = false,
}) {
  const [editing, setEditing] = useState(false);
  const [tags, setTags] = useState([]);
  const [notes, setNotes] = useState("");
  const [resourceClass, setResourceClass] = useState("media");
  const [showRaw, setShowRaw] = useState(false);

  useEffect(() => {
    setTags(item?.tags || []);
    setNotes(item?.notes || "");
    setResourceClass(item?.resource_class || "media");
    setEditing(false);
    setShowRaw(false);
  }, [item?.id]);

  const summary = item?.summary || {};

  if (!open) return null;

  return (
    <div
      className="resource-modal-overlay"
      onClick={onClose}
      role="presentation"
    >
      <div
        className={`resource-modal-dialog ${showRaw ? "has-raw-expanded" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="resource-modal-title"
        onClick={(e) => e.stopPropagation()}
      >
        {loading || !item ? (
          <div className="resource-detail-loading">
            <LoaderCircle size={22} className="spin" />
            <span>正在载入来源详情...</span>
          </div>
        ) : (
          <>
            <header className="resource-modal-head">
              <div className="resource-modal-title-box">
                <div className="resource-modal-badge-row">
                  <span className="resource-modal-id">来源编号 #{item.id}</span>
                  <span className="resource-class-badge">
                    {CLASS_LABELS[item.resource_class] || "其他"}
                  </span>
                  {item.tmdb_status === "matched" ? (
                    <span className="resource-tmdb-link">
                      TMDB #{item.tmdb_id}
                      {item.media_id && onViewMedia && (
                        <button
                          type="button"
                          className="resource-view-media-btn"
                          onClick={() => onViewMedia(item.media_id)}
                        >
                          在影视库查看 →
                        </button>
                      )}
                    </span>
                  ) : (
                    <span className="resource-tmdb-unmatched">
                      {TMDB_LABELS[item.tmdb_status] || "未识别"}
                    </span>
                  )}
                </div>
                <h2 id="resource-modal-title" title={item.title}>
                  {item.title || "未命名资源"}
                </h2>
              </div>
              <button
                type="button"
                className="icon-only resource-modal-close"
                onClick={onClose}
                aria-label="关闭详情弹窗"
              >
                <X size={18} />
              </button>
            </header>

            <div className="resource-modal-body">
              {/* Left Column: Actions, Share Info, Attributes & Related Sources */}
              <div className="resource-modal-col resource-modal-col-left">
                {/* Primary Actions Bar */}
                <div className="resource-detail-actions">
                  <button
                    type="button"
                    className="re0-btn re0-btn-primary"
                    onClick={() => onAction(item.id, "copy")}
                  >
                    <Clipboard size={14} />
                    <span>复制链接</span>
                  </button>
                  <button
                    type="button"
                    className="re0-btn re0-btn-ghost"
                    onClick={() => onAction(item.id, "open")}
                  >
                    <ExternalLink size={14} />
                    <span>打开分享</span>
                  </button>
                  <button
                    type="button"
                    className="re0-btn re0-btn-ghost icon-only"
                    disabled={checking || !item.provider?.can_check}
                    onClick={() => onCheck(item.id)}
                    title="重新检查链接有效性"
                  >
                    <RefreshCw size={14} className={checking ? "spin" : ""} />
                  </button>
                </div>

                {/* Share Information */}
                <section className="resource-detail-section first">
                  <h3 className="resource-section-title">分享信息</h3>
                  <div className="resource-detail-link">
                    <span>分享链接</span>
                    <code>{item.url_masked || "—"}</code>
                    <CopyButton value={item.url} label="复制" />
                  </div>
                </section>

                {/* Status and Technical Facts */}
                <section className="resource-detail-section">
                  <div className="resource-section-head">
                    <h3 className="resource-section-title">状态与属性</h3>
                    <button
                      type="button"
                      className="re0-btn small"
                      onClick={() => setEditing((v) => !v)}
                    >
                      {editing ? "取消" : "编辑"}
                    </button>
                  </div>

                  {editing ? (
                    <form
                      className="resource-edit-form"
                      onSubmit={(e) => {
                        e.preventDefault();
                        onUpdate({ resource_class: resourceClass, tags, notes });
                        setEditing(false);
                      }}
                    >
                      <label>
                        <span>资源类型</span>
                        <select
                          value={resourceClass}
                          onChange={(e) => setResourceClass(e.target.value)}
                        >
                          {Object.entries(CLASS_LABELS).map(([v, l]) => (
                            <option value={v} key={v}>
                              {l}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label>
                        <span>标签 (以空格分隔)</span>
                        <input
                          value={tags.join(" ")}
                          onChange={(e) =>
                            setTags(e.target.value.split(/[\s,，]+/).filter(Boolean))
                          }
                          placeholder="4K 国英双语 REMUX"
                        />
                      </label>
                      <label>
                        <span>备注说明</span>
                        <textarea
                          value={notes}
                          onChange={(e) => setNotes(e.target.value)}
                          rows="3"
                          placeholder="输入资源备注..."
                        />
                      </label>
                      <button type="submit" className="re0-btn re0-btn-primary small">
                        保存修改
                      </button>
                    </form>
                  ) : (
                    <>
                      <dl className="resource-detail-facts">
                        <DetailValue label="网盘平台">
                          <ProviderMark provider={item.provider} />
                        </DetailValue>
                        <DetailValue label="来源状态">
                          <StatusDot status={item.availability_status} />
                        </DetailValue>
                        <DetailValue label="收录频道">{item.channel || "未知频道"}</DetailValue>
                        <DetailValue label="更新时间">
                          {formatDateTime(item.last_checked_at || item.updated_at)}
                        </DetailValue>
                        <DetailValue label="文件体积">
                          {formatBytes(summary.total_size)}
                        </DetailValue>
                        {(summary.quality_tags || []).length > 0 && (
                          <DetailValue label="技术规格">
                            {(summary.quality_tags || []).join(" · ")}
                          </DetailValue>
                        )}
                        {item.notes && <DetailValue label="备注说明">{item.notes}</DetailValue>}
                      </dl>
                      {item.last_check_error && (
                        <div className="resource-check-error">
                          {item.last_check_error}
                        </div>
                      )}
                    </>
                  )}
                </section>

                {/* Related Sources */}
                {item.related_sources?.length > 0 && (
                  <section className="resource-detail-section">
                    <h3 className="resource-section-title">
                      同一作品的其它网盘 ({item.related_sources.length})
                    </h3>
                    <div className="resource-related-grid">
                      {item.related_sources.map((s) => (
                        <button
                          type="button"
                          key={s.id}
                          className="resource-related-item"
                          onClick={() => onAction(s.id, "select")}
                        >
                          <ProviderMark provider={s.provider} compact />
                          <span className="related-title" title={s.title}>{s.title}</span>
                          <StatusDot status={s.availability_status} />
                        </button>
                      ))}
                    </div>
                  </section>
                )}

                {/* Toggle Button to Expand Raw Messages on the Right */}
                <section className="resource-detail-section">
                  <button
                    type="button"
                    className={`resource-raw-trigger-btn ${showRaw ? "active" : ""}`}
                    onClick={() => setShowRaw(!showRaw)}
                    title={showRaw ? "收起右侧原始消息面板" : "在右侧展开原始消息记录"}
                  >
                    <div className="raw-trigger-left">
                      <FileText size={15} />
                      <span>原始消息记录 ({item.original_messages?.length || 1})</span>
                    </div>
                    <span className="raw-trigger-hint">
                      {showRaw ? "收起右侧 ✕" : "在右侧展开 →"}
                    </span>
                  </button>
                </section>
              </div>

              {/* Right Column: Raw Telegram Messages Expanded on Demand */}
              {showRaw && (
                <div className="resource-modal-col resource-modal-col-right">
                  <section className="resource-raw-messages-section">
                    <div className="resource-raw-header">
                      <div className="resource-raw-title-wrap">
                        <FileText size={15} className="resource-raw-icon" />
                        <h3 className="resource-section-title">原始消息记录</h3>
                        <span className="resource-raw-badge">
                          {item.original_messages?.length ? `${item.original_messages.length} 条记录` : "1 条记录"}
                        </span>
                      </div>
                      <button
                        type="button"
                        className="resource-raw-close-btn"
                        onClick={() => setShowRaw(false)}
                        title="收起右侧消息"
                        aria-label="收起右侧原始消息"
                      >
                        <X size={14} />
                      </button>
                    </div>

                    <div className="resource-raw-messages-list">
                      {item.original_messages?.length ? (
                        item.original_messages.map((m) => <RawMessageCard key={m.id} message={m} />)
                      ) : (
                        <article className="resource-raw-card">
                          <div className="resource-raw-card-head">
                            <strong>{item.channel || "入库消息"}</strong>
                            <small className="resource-raw-time">
                              {formatDateTime(item.created_at || item.updated_at)}
                            </small>
                          </div>
                          <div className="resource-raw-content">
                            <pre>{item.raw_text || "没有保存原始消息。"}</pre>
                          </div>
                        </article>
                      )}
                      {item.repeat_messages?.length ? (
                        <details className="resource-raw-repeats">
                          <summary>
                            另有 {item.repeat_messages.length} 条消息发过同一链接（重复投稿，默认收起）
                          </summary>
                          {item.repeat_messages.map((m) => (
                            <RawMessageCard key={m.id} message={m} repeat />
                          ))}
                        </details>
                      ) : null}
                    </div>
                  </section>
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
