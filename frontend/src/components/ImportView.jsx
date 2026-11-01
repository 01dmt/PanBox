import { useCallback, useEffect, useRef, useState } from "react";
import {
  CheckCircle2,
  ChevronRight,
  Clipboard,
  ClipboardCheck,
  Clock3,
  ExternalLink,
  FolderInput,
  History,
  Inbox,
  ListFilter,
  LoaderCircle,
  RadioTower,
  RefreshCw,
  Search,
  Send,
  Sparkles,
  UploadCloud,
  X,
} from "lucide-react";
import { getIngestionRecord, getIngestionRecords, importText, reprocessIgnoredIngestion, retryFailedIngestions, retryIngestionRecord } from "../api";
import { formatDateTime, formatNumber } from "../lib/format";

const SOURCE_LABELS = { telegram: "Telegram", discord: "Discord", webhook: "Webhook" };
const INGESTION_STATUS_LABELS = {
  imported: "已入库", duplicate: "重复", ignored: "已忽略", failed: "失败",
  pending_validation: "等待链接校验", queued: "排队中", rejected: "已拒绝",
};
const STATUS_TONES = {
  imported: "success",
  duplicate: "info",
  ignored: "muted",
  failed: "danger",
  pending_validation: "warning",
  queued: "info",
  rejected: "danger",
};
const STATUS_FILTERS = [
  ["all", "全部状态"], ["imported", "已入库"], ["failed", "失败"], ["duplicate", "重复"], ["ignored", "已忽略"],
];
const RETRYABLE_LINK_STATUSES = ["invalid", "error", "pending_validation"];
const LINK_STATUS_LABELS = {
  pending_validation: "等待校验", invalid: "无效/密码错误", error: "校验错误",
  inserted: "已入库", duplicate: "重复", validated: "校验通过",
};
const LINK_STATUS_TONES = {
  inserted: "success",
  validated: "success",
  duplicate: "muted",
  invalid: "danger",
  error: "danger",
  pending_validation: "warning",
};

function TelegramIcon({ size = 16 }) {
  return <svg aria-hidden="true" width={size} height={size} viewBox="0 0 24 24" fill="currentColor"><path d="M21.7 3.3 18.5 20c-.25 1.18-.91 1.47-1.84.92l-5.1-3.76-2.46 2.37c-.27.27-.5.5-1.02.5l.37-5.2 9.46-8.55c.41-.37-.09-.58-.64-.21L5.58 13.3.52 11.72c-1.1-.35-1.12-1.1.23-1.6L20.5 2.45c.92-.34 1.73.21 1.2.85Z" /></svg>;
}

function statusTone(status) {
  return STATUS_TONES[status] || "muted";
}

function safeHttpUrl(value) {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) ? url.href : "";
  } catch {
    return "";
  }
}

function ChannelAvatar({ url, size = 38 }) {
  return (
    <span className="ingestion-avatar" style={{ "--avatar-size": `${size}px` }}>
      <span className="ingestion-avatar-fallback"><Send size={Math.max(14, Math.round(size * 0.42))} /></span>
      {url ? (
        <img
          key={url}
          src={url}
          alt=""
          loading="lazy"
          onError={(event) => { event.currentTarget.style.display = "none"; }}
        />
      ) : null}
    </span>
  );
}

function mediaSummary(item) {
  const details = item.media_details?.length ? item.media_details : item.media || [];
  const first = details[0];
  return {
    title: first?.title || item.media_titles?.[0] || "",
    meta: first ? [first.year, ...(first.episode_labels || [])].filter(Boolean).join(" · ") : "",
    extra: Math.max(0, details.length - 1),
  };
}


export default function ImportView({ onImported, manualOpen, onCloseManual, initialStatus = "all", status, onStatusChange }) {
  const [records, setRecords] = useState([]);
  const [todayCount, setTodayCount] = useState(0);
  const [statusFilterState, setStatusFilterState] = useState(initialStatus);
  const statusFilter = status ?? statusFilterState;
  const setStatusFilter = (value) => {
    if (onStatusChange) onStatusChange(value);
    else setStatusFilterState(value);
  };
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [recordsError, setRecordsError] = useState("");
  const [recordsLoading, setRecordsLoading] = useState(false);
  const [selectedRecord, setSelectedRecord] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [copied, setCopied] = useState(false);
  const [reprocessing, setReprocessing] = useState(false);
  const [reprocessNotice, setReprocessNotice] = useState(null);
  const [retrying, setRetrying] = useState(false);
  const [retryingRecord, setRetryingRecord] = useState(false);
  const [toast, setToast] = useState(null);
  const reprocessNoticeTimer = useRef(null);
  const toastTimer = useRef(null);
  const inputRef = useRef(null);

  const showToast = useCallback((tone, text, duration = 3600) => {
    setToast({ tone, text });
    window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(null), duration);
  }, []);

  useEffect(() => () => {
    window.clearTimeout(reprocessNoticeTimer.current);
    window.clearTimeout(toastTimer.current);
  }, []);

  const refresh = useCallback(async () => {
    setRecordsLoading(true);
    try {
      const payload = await getIngestionRecords(statusFilter, search);
      setRecords(payload.items || []);
      setTodayCount(payload.today_count || 0);
      setRecordsError("");
    } catch (nextError) {
      setRecordsError(nextError.message);
    } finally {
      setRecordsLoading(false);
    }
  }, [statusFilter, search]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    const timer = window.setTimeout(() => setSearch(searchInput.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [searchInput]);

  useEffect(() => {
    if (!manualOpen) return;
    setResult(null);
    setError("");
  }, [manualOpen]);

  useEffect(() => {
    if (!selectedRecord && !manualOpen) return undefined;
    const onKeyDown = (event) => {
      if (event.key !== "Escape") return;
      if (selectedRecord) setSelectedRecord(null);
      else if (!busy) onCloseManual();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [busy, manualOpen, onCloseManual, selectedRecord]);

  const resetFilters = () => {
    setSearchInput("");
    setSearch("");
    setStatusFilter("all");
  };

  const reprocessIgnored = async () => {
    setReprocessing(true);
    setReprocessNotice({ tone: "progress", text: "正在提交重新识别任务…" });
    try {
      const nextResult = await reprocessIgnoredIngestion(100);
      const queued = Number(nextResult.queued || 0);
      setReprocessNotice({ tone: queued ? "progress" : "success", text: queued ? `已提交 ${queued} 条，正在后台重新识别` : "当前没有待重新识别的记录" });
      window.clearTimeout(reprocessNoticeTimer.current);
      reprocessNoticeTimer.current = window.setTimeout(() => setReprocessNotice(null), 4500);
      refresh();
    } catch (nextError) {
      setReprocessNotice({ tone: "error", text: `重新识别失败：${nextError.message}` });
      window.clearTimeout(reprocessNoticeTimer.current);
      reprocessNoticeTimer.current = window.setTimeout(() => setReprocessNotice(null), 5000);
    } finally {
      setReprocessing(false);
    }
  };

  const retryFailed = async () => {
    setRetrying(true);
    setReprocessNotice({ tone: "progress", text: "正在提交失败记录重试…" });
    try {
      const nextResult = await retryFailedIngestions(100);
      const events = Number(nextResult.queued || 0);
      setReprocessNotice({ tone: events ? "progress" : "success", text: events ? `已提交 ${events} 条失败记录，正在后台重新校验` : "当前没有可重试的失败记录" });
      window.clearTimeout(reprocessNoticeTimer.current);
      reprocessNoticeTimer.current = window.setTimeout(() => setReprocessNotice(null), 4500);
      refresh();
    } catch (nextError) {
      setReprocessNotice({ tone: "error", text: `重试提交失败：${nextError.message}` });
      window.clearTimeout(reprocessNoticeTimer.current);
      reprocessNoticeTimer.current = window.setTimeout(() => setReprocessNotice(null), 5000);
    } finally {
      setRetrying(false);
    }
  };

  const watchRetryOutcome = async (recordId) => {
    const deadline = Date.now() + 30000;
    while (Date.now() < deadline) {
      await new Promise((resolve) => window.setTimeout(resolve, 3000));
      try {
        const record = await getIngestionRecord(recordId);
        const links = record.links || [];
        if (links.some((link) => ["pending_validation", "queued"].includes(link.status))) continue;
        setSelectedRecord((current) => (current && current.id === recordId ? record : current));
        refresh();
        const failed = links.filter((link) => ["invalid", "error"].includes(link.status));
        const imported = links.filter((link) => ["inserted", "validated"].includes(link.status));
        if (failed.length) {
          const reason = failed[0].validation_error || LINK_STATUS_LABELS[failed[0].status] || failed[0].status;
          showToast("error", `重新校验未通过：${failed.length} 条来源仍失败（${reason}）`, 5200);
        } else if (imported.length) {
          showToast("success", `重新校验成功：${imported.length} 条来源已入库`, 4200);
        } else {
          showToast("success", "重新校验完成，没有新的入库来源", 4200);
        }
        return;
      } catch {
        // Keep polling until the deadline; transient read errors are not a result.
      }
    }
    showToast("progress", "重新校验仍在后台进行，可稍后刷新查看结果", 4600);
  };

  const retryRecord = async () => {
    if (!selectedRecord) return;
    const recordId = selectedRecord.id;
    setRetryingRecord(true);
    showToast("progress", "正在提交重新校验…", 2600);
    try {
      const nextResult = await retryIngestionRecord(recordId);
      const count = Number(nextResult.queued || 0);
      setSelectedRecord(await getIngestionRecord(recordId));
      refresh();
      if (!count) {
        showToast("success", "当前记录没有需要重新校验的来源");
        return;
      }
      showToast("progress", `已提交 ${count} 条来源，正在重新校验…`, 2600);
      await watchRetryOutcome(recordId);
    } catch (nextError) {
      showToast("error", `重试提交失败：${nextError.message}`, 5000);
    } finally {
      setRetryingRecord(false);
    }
  };

  const handleFiles = async (files) => {
    const file = files?.[0];
    if (!file) return;
    setBusy(true);
    setError("");
    try {
      const content = await file.text();
      const nextResult = await importText(file.name, content, "auto");
      setResult(nextResult);
      refresh();
      await onImported(nextResult);
      onCloseManual();
    } catch (nextError) {
      setError(nextError.message);
    } finally {
      setBusy(false);
    }
  };

  const openRecord = async (record) => {
    setDetailLoading(true);
    setCopied(false);
    try {
      setSelectedRecord(await getIngestionRecord(record.id));
    } catch (nextError) {
      showToast("error", `加载消息明细失败：${nextError.message}`, 5000);
    } finally {
      setDetailLoading(false);
    }
  };

  const copyMessage = async () => {
    if (!selectedRecord?.raw_text || !navigator.clipboard) return;
    try {
      await navigator.clipboard.writeText(selectedRecord.raw_text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch (nextError) {
      showToast("error", `复制失败：${nextError.message}`, 4200);
    }
  };

  const filtered = Boolean(search || statusFilter !== "all");
  const displayLabel = search ? "搜索结果" : statusFilter === "all" ? "显示记录" : "筛选结果";
  const recordMessageUrl = selectedRecord ? safeHttpUrl(selectedRecord.message_url) : "";

  return (
    <main className="page-view import-view">
      <div className="ingestion-page">
        <section className="ingestion-hero" aria-labelledby="ingestion-title">
          <div className="ingestion-hero-copy">
            <span className="ingestion-eyebrow"><RadioTower size={13} aria-hidden="true" />Ingestion log</span>
            <h1 id="ingestion-title">入库记录</h1>
            <p>追踪外部频道消息的接收、解析、链接校验与最终入库状态，点击任意记录可以查看完整原始消息。</p>
            <div className="ingestion-hero-actions">
              <button type="button" className="ingestion-action-button" onClick={retryFailed} disabled={retrying}>
                {retrying ? <LoaderCircle size={15} className="spin" /> : <RefreshCw size={15} />}
                {retrying ? "提交中…" : "重试失败记录"}
              </button>
              <button type="button" className="ingestion-action-button" onClick={reprocessIgnored} disabled={reprocessing}>
                {reprocessing ? <LoaderCircle size={15} className="spin" /> : <Sparkles size={15} />}
                {reprocessing ? "识别中…" : "重新识别未识别媒体"}
              </button>
            </div>
            {reprocessNotice ? (
              <span className={`ingestion-notice ingestion-notice--${reprocessNotice.tone}`} role="status" aria-live="polite">
                {reprocessNotice.tone === "progress" ? <LoaderCircle size={13} className="spin" /> : reprocessNotice.tone === "error" ? <X size={13} /> : <CheckCircle2 size={13} />}
                {reprocessNotice.text}
              </span>
            ) : null}
          </div>

          <div className="ingestion-hero-stats">
            <article className="ingestion-stat ingestion-stat--primary">
              <span className="ingestion-stat-icon"><History size={18} /></span>
              <div className="ingestion-stat-body">
                <span className="ingestion-stat-label">今日入库</span>
                <strong>{formatNumber(todayCount)}</strong>
                <small>条消息</small>
              </div>
            </article>
            <article className="ingestion-stat">
              <span className="ingestion-stat-icon"><ListFilter size={18} /></span>
              <div className="ingestion-stat-body">
                <span className="ingestion-stat-label">{displayLabel}</span>
                <strong>{formatNumber(records.length)}</strong>
                <small>{search ? "条匹配记录" : statusFilter === "all" ? "条近期记录" : "条筛选记录"}</small>
              </div>
            </article>
          </div>
        </section>

        {recordsError ? (
          <div className="ingestion-error" role="alert">
            <span><strong>入库记录加载失败</strong>{recordsError}</span>
            <button type="button" onClick={refresh} disabled={recordsLoading}>
              {recordsLoading ? <LoaderCircle size={14} className="spin" /> : <RefreshCw size={14} />}
              重新加载
            </button>
          </div>
        ) : null}

        <section className="import-history" aria-labelledby="ingestion-list-title">
          <header className="ingestion-toolbar">
            <div className="ingestion-toolbar-title">
              <h2 id="ingestion-list-title">消息流水</h2>
              <span>{formatNumber(records.length)} 条</span>
            </div>
            <div className="ingestion-toolbar-controls">
              <div className="ingestion-search" role="search">
                <Search size={15} aria-hidden="true" />
                <input
                  type="search"
                  value={searchInput}
                  onChange={(event) => setSearchInput(event.target.value)}
                  placeholder="搜索频道、媒体、链接或原文"
                  aria-label="搜索入库记录"
                />
                {searchInput ? <button type="button" onClick={() => setSearchInput("")} aria-label="清除搜索"><X size={13} /></button> : null}
              </div>
              <label className="ingestion-filter">
                <ListFilter size={14} aria-hidden="true" />
                <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)} aria-label="按状态筛选">
                  {STATUS_FILTERS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                </select>
              </label>
              <button
                type="button"
                className="ingestion-refresh"
                onClick={refresh}
                disabled={recordsLoading}
                aria-label="刷新入库记录"
                title="刷新入库记录"
              >
                <RefreshCw size={15} className={recordsLoading ? "spin" : ""} />
              </button>
            </div>
          </header>

          <div className={`ingestion-list ${recordsLoading ? "is-loading" : ""}`} aria-busy={recordsLoading}>
            <div className="ingestion-list-head" aria-hidden="true">
              <span>消息来源频道</span>
              <span>来源渠道</span>
              <span>入库媒体</span>
              <span>状态</span>
              <span>接收时间</span>
              <span />
            </div>
            {records.map((item) => {
              const media = mediaSummary(item);
              return (
                <button
                  className="ingestion-row"
                  type="button"
                  key={item.id}
                  onClick={() => openRecord(item)}
                  aria-haspopup="dialog"
                  aria-label={`查看 ${item.source_channel || "未知频道"} 的入库明细，状态：${INGESTION_STATUS_LABELS[item.status] || item.status}，接收时间：${formatDateTime(item.received_at)}`}
                >
                  <span className="ingestion-channel">
                    <ChannelAvatar url={item.channel_avatar_url} />
                    <span className="ingestion-channel-copy">
                      <strong>{item.source_channel || item.channel_name || "未知频道"}</strong>
                      <small>{item.source_username ? `@${item.source_username}` : item.channel_id ? `频道 ID ${item.channel_id}` : "来源信息缺失"}</small>
                    </span>
                  </span>
                  <span className="ingestion-service">
                    <TelegramIcon size={14} />
                    <span>{SOURCE_LABELS[item.source_service] || item.source_service || "未知"}</span>
                  </span>
                  <span className={`ingestion-media ${media.title ? "" : "is-empty"}`} title={item.media_titles?.join("、")}>
                    {media.title ? (
                      <>
                        <strong>{media.title}</strong>
                        <small>
                          {media.meta || "已识别媒体"}
                          {media.extra ? <em>+{media.extra} 部</em> : null}
                        </small>
                      </>
                    ) : (
                      <span>未识别媒体</span>
                    )}
                  </span>
                  <span className="ingestion-state">
                    <span className={`ingestion-status ingestion-status--${statusTone(item.status)}`}>
                      <i aria-hidden="true" />
                      {INGESTION_STATUS_LABELS[item.status] || item.status}
                    </span>
                    {item.error ? <small className="ingestion-status-reason" title={item.error}>{item.error}</small> : null}
                  </span>
                  <time className="ingestion-time" dateTime={item.received_at} title={item.received_at || undefined}>
                    <Clock3 size={12} aria-hidden="true" />
                    <span>{formatDateTime(item.received_at)}</span>
                  </time>
                  <span className="ingestion-chevron" aria-hidden="true"><ChevronRight size={16} /></span>
                </button>
              );
            })}

            {!records.length && recordsLoading ? (
              <div className="ingestion-empty ingestion-empty--loading">
                <LoaderCircle size={22} className="spin" />
                <strong>正在加载入库记录</strong>
              </div>
            ) : null}
            {!records.length && !recordsLoading && !recordsError ? (
              <div className="ingestion-empty">
                <span className="ingestion-empty-icon"><Inbox size={22} /></span>
                <strong>{filtered ? "没有符合条件的入库记录" : "还没有入库记录"}</strong>
                <p>{filtered ? "换一个关键词或状态再试试。" : "频道消息入库后，会按接收时间显示在这里。"}</p>
                {filtered ? <button type="button" onClick={resetFilters}>清除筛选</button> : null}
              </div>
            ) : null}
          </div>

          <footer className="ingestion-list-foot">
            <span><RadioTower size={12} aria-hidden="true" />最新接收的记录优先显示</span>
            <span>点击记录查看完整消息与来源链接</span>
          </footer>
        </section>
      </div>

      {detailLoading ? (
        <div className="record-detail-backdrop" role="presentation">
          <section className="record-detail-modal record-detail-loading" role="dialog" aria-modal="true" aria-label="加载消息明细">
            <LoaderCircle size={24} className="spin" />正在加载消息明细
          </section>
        </div>
      ) : null}

      {selectedRecord ? (
        <div className="record-detail-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setSelectedRecord(null); }}>
          <section className="record-detail-modal" role="dialog" aria-modal="true" aria-labelledby="record-detail-title">
            <header className="record-detail-head">
              <div className="record-detail-heading">
                <ChannelAvatar url={selectedRecord.channel_avatar_url} size={48} />
                <div className="record-detail-title">
                  <span className="record-detail-eyebrow">Ingestion detail</span>
                  <h2 id="record-detail-title">{selectedRecord.channel_name || selectedRecord.channel_id || "未知频道"}</h2>
                  <div className="record-detail-meta">
                    <span><Send size={11} aria-hidden="true" />{SOURCE_LABELS[selectedRecord.service] || selectedRecord.service || "未知渠道"}</span>
                    {selectedRecord.channel_username ? <span>@{selectedRecord.channel_username}</span> : null}
                    <span><Clock3 size={11} aria-hidden="true" />接收于 {formatDateTime(selectedRecord.received_at)}</span>
                    {recordMessageUrl ? (
                      <a href={recordMessageUrl} target="_blank" rel="noreferrer noopener">
                        查看原消息<ExternalLink size={11} aria-hidden="true" />
                      </a>
                    ) : (
                      <span>消息 ID：{selectedRecord.message_id || "未知"}</span>
                    )}
                  </div>
                </div>
              </div>
              <div className="record-detail-head-actions">
                <span className={`ingestion-status ingestion-status--${statusTone(selectedRecord.status)}`}>
                  <i aria-hidden="true" />
                  {INGESTION_STATUS_LABELS[selectedRecord.status] || selectedRecord.status}
                </span>
                <button type="button" className="icon-only" onClick={() => setSelectedRecord(null)} aria-label="关闭消息明细"><X size={17} /></button>
              </div>
            </header>

            <div className="record-detail-body">
              {selectedRecord.error ? (
                <div className="record-detail-reason" role="status">
                  <X size={14} />
                  <span><strong>{INGESTION_STATUS_LABELS[selectedRecord.status] || "处理失败"}</strong>：{selectedRecord.error}</span>
                </div>
              ) : null}

              <div className="record-detail-section">
                <div className="record-detail-label">
                  <div>
                    <strong>原始消息</strong>
                    <span>{formatNumber(selectedRecord.raw_text?.length || 0)} 字符</span>
                  </div>
                  <button type="button" className="button small" onClick={copyMessage} disabled={!selectedRecord.raw_text}>
                    {copied ? <ClipboardCheck size={14} /> : <Clipboard size={14} />}
                    {copied ? "已复制" : "复制"}
                  </button>
                </div>
                <pre className="record-detail-message">{selectedRecord.raw_text || "（无原始消息）"}</pre>
              </div>

              <div className="record-detail-section">
                <div className="record-detail-label">
                  <div>
                    <strong>识别来源</strong>
                    <span>{selectedRecord.links?.length || 0} 条链接</span>
                  </div>
                  {selectedRecord.links?.some((link) => RETRYABLE_LINK_STATUSES.includes(link.status)) ? (
                    <button type="button" className="button small" onClick={retryRecord} disabled={retryingRecord}>
                      {retryingRecord ? <LoaderCircle size={14} className="spin" /> : <RefreshCw size={14} />}
                      {retryingRecord ? "提交中…" : "重新校验并入库"}
                    </button>
                  ) : null}
                </div>
                {selectedRecord.links?.length ? (
                  <div className="record-detail-links">
                    {selectedRecord.links.map((link) => (
                      <div key={link.id || link.source_key}>
                        <div className="record-link-head">
                          <span className="record-link-provider">{link.provider || "来源"}</span>
                          <span className={`record-link-status record-link-status--${LINK_STATUS_TONES[link.status] || "muted"}`}>
                            <i aria-hidden="true" />
                            {LINK_STATUS_LABELS[link.status] || link.status}
                          </span>
                        </div>
                        <code title={link.url}>{link.url}</code>
                        {link.validation_error ? <small>{link.validation_error}</small> : null}
                      </div>
                    ))}
                  </div>
                ) : <p className="record-detail-empty">没有提取到 115、123、光鸭或 ED2K 来源。</p>}
              </div>

              <details className="record-detail-raw">
                <summary>查看接收 JSON</summary>
                <pre>{JSON.stringify(selectedRecord.raw_json, null, 2)}</pre>
              </details>
            </div>
          </section>
        </div>
      ) : null}

      {manualOpen ? (
        <div className="manual-import-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) onCloseManual(); }}>
          <section className="manual-import-modal" role="dialog" aria-modal="true" aria-labelledby="manual-import-title">
            <header className="manual-import-head">
              <div>
                <span className="manual-import-eyebrow">Manual ingest</span>
                <h2 id="manual-import-title">手动导入文本清单</h2>
                <p>把已有的网盘分享或 ED2K 链接一次性补录进本地资料库。</p>
              </div>
              <button type="button" className="icon-only" onClick={onCloseManual} disabled={busy} aria-label="关闭手动入库"><X size={17} /></button>
            </header>
            <div
              className={`import-zone ${dragging ? "dragging" : ""}`}
              onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
              onDragLeave={() => setDragging(false)}
              onDrop={(event) => { event.preventDefault(); setDragging(false); handleFiles(event.dataTransfer.files); }}
            >
              <span className="import-zone-icon">
                {busy ? <LoaderCircle size={26} className="spin" /> : <UploadCloud size={26} />}
              </span>
              <h2>{busy ? "正在解析并写入数据库" : "拖入文本清单，或从本地选择"}</h2>
              <p>支持 UTF-8 文本，自动识别 115、123、光鸭分享链接及一行多个 ED2K 链接。</p>
              <button type="button" className="button primary" onClick={() => inputRef.current?.click()} disabled={busy}>
                <FolderInput size={16} />选择文本文件
              </button>
              <input ref={inputRef} type="file" accept=".txt,text/plain" hidden onChange={(event) => handleFiles(event.target.files)} />
            </div>
            {error ? <div className="inline-error">{error}</div> : null}
            {result ? (
              <div className="import-result">
                <CheckCircle2 size={20} />
                <div><strong>{result.source_name}</strong><span>识别 {formatNumber(result.total)} · 新增 {formatNumber(result.inserted)} · 重复 {formatNumber(result.duplicates)} · 错误 {formatNumber(result.errors)}</span></div>
              </div>
            ) : null}
          </section>
        </div>
      ) : null}
      {toast ? (
        <div className={`light-toast light-toast--${toast.tone}`} role="status" aria-live="polite">
          {toast.tone === "progress" ? <LoaderCircle size={15} className="spin" /> : toast.tone === "error" ? <X size={15} /> : <CheckCircle2 size={15} />}
          <span>{toast.text}</span>
        </div>
      ) : null}
    </main>
  );
}
