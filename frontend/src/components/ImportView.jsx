import { useCallback, useEffect, useRef, useState } from "react";
import { CheckCircle2, Clipboard, ClipboardCheck, FolderInput, LoaderCircle, RefreshCw, Search, Send, UploadCloud, X } from "lucide-react";
import { getIngestionRecord, getIngestionRecords, importText, reprocessIgnoredIngestion, retryFailedIngestions, retryIngestionRecord } from "../api";
import { formatDateTime, formatNumber } from "../lib/format";

const SOURCE_LABELS = { telegram: "Telegram", discord: "Discord", webhook: "Webhook" };
const INGESTION_STATUS_LABELS = {
  imported: "已入库", duplicate: "重复", ignored: "已忽略", failed: "失败",
  pending_validation: "等待链接校验", queued: "排队中", rejected: "已拒绝",
};
const STATUS_FILTERS = [
  ["all", "全部状态"], ["failed", "失败"], ["pending_validation", "等待链接校验"],
  ["imported", "已入库"], ["duplicate", "重复"], ["ignored", "已忽略"], ["queued", "排队中"],
];
const RETRYABLE_LINK_STATUSES = ["invalid", "error", "pending_validation"];
const LINK_STATUS_LABELS = {
  pending_validation: "等待校验", invalid: "无效/密码错误", error: "校验错误",
  inserted: "inserted", duplicate: "duplicate", validated: "validated",
};

function TelegramIcon({ size = 16 }) {
  return <svg aria-hidden="true" width={size} height={size} viewBox="0 0 24 24" fill="currentColor"><path d="M21.7 3.3 18.5 20c-.25 1.18-.91 1.47-1.84.92l-5.1-3.76-2.46 2.37c-.27.27-.5.5-1.02.5l.37-5.2 9.46-8.55c.41-.37-.09-.58-.64-.21L5.58 13.3.52 11.72c-1.1-.35-1.12-1.1.23-1.6L20.5 2.45c.92-.34 1.73.21 1.2.85Z" /></svg>;
}


export default function ImportView({ onImported, manualOpen, onCloseManual }) {
  const [records, setRecords] = useState([]);
  const [todayCount, setTodayCount] = useState(0);
  const [statusFilter, setStatusFilter] = useState("all");
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
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

  const refresh = useCallback(() => {
    getIngestionRecords(statusFilter, search).then((payload) => {
      setRecords(payload.items || []);
      setTodayCount(payload.today_count || 0);
    }).catch((nextError) => setError(nextError.message));
  }, [statusFilter, search]);

  useEffect(refresh, [refresh]);

  useEffect(() => {
    const timer = window.setTimeout(() => setSearch(searchInput.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [searchInput]);

  const reprocessIgnored = async () => {
    setReprocessing(true);
    setError("");
    setReprocessNotice({ tone: "progress", text: "正在提交重新识别任务…" });
    try {
      const result = await reprocessIgnoredIngestion(100);
      const queued = Number(result.queued || 0);
      setResult({ source_name: "重新识别", total: queued, inserted: 0, duplicates: 0, errors: 0 });
      setReprocessNotice({ tone: queued ? "progress" : "success", text: queued ? `已提交 ${queued} 条，正在后台重新识别` : "当前没有待重新识别的记录" });
      window.clearTimeout(reprocessNoticeTimer.current);
      reprocessNoticeTimer.current = window.setTimeout(() => setReprocessNotice(null), 4500);
      refresh();
    } catch (nextError) {
      setError(nextError.message);
      setReprocessNotice({ tone: "error", text: `重新识别失败：${nextError.message}` });
      window.clearTimeout(reprocessNoticeTimer.current);
      reprocessNoticeTimer.current = window.setTimeout(() => setReprocessNotice(null), 5000);
    } finally {
      setReprocessing(false);
    }
  };

  const retryFailed = async () => {
    setRetrying(true);
    setError("");
    setReprocessNotice({ tone: "progress", text: "正在提交失败记录重试…" });
    try {
      const result = await retryFailedIngestions(100);
      const events = Number(result.queued || 0);
      setReprocessNotice({ tone: events ? "progress" : "success", text: events ? `已提交 ${events} 条失败记录，正在后台重新校验` : "当前没有可重试的失败记录" });
      window.clearTimeout(reprocessNoticeTimer.current);
      reprocessNoticeTimer.current = window.setTimeout(() => setReprocessNotice(null), 4500);
      refresh();
    } catch (nextError) {
      setError(nextError.message);
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
      const result = await retryIngestionRecord(recordId);
      const count = Number(result.queued || 0);
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
    setError("");
    try {
      setSelectedRecord(await getIngestionRecord(record.id));
    } catch (nextError) {
      setError(nextError.message);
    } finally {
      setDetailLoading(false);
    }
  };

  const copyMessage = async () => {
    if (!selectedRecord?.raw_text || !navigator.clipboard) return;
    await navigator.clipboard.writeText(selectedRecord.raw_text);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1800);
  };

  return (
    <main className="page-view import-view">
      <section className="import-history">
        <div className="ingestion-summary">
          <div><span>今日入库数量</span><strong>{formatNumber(todayCount)}</strong><small>条消息</small></div>
          <div><span>显示记录</span><strong>{formatNumber(records.length)}</strong><small>{search ? "搜索结果" : statusFilter === "all" ? "最近 100 条" : "筛选结果"}</small></div>
        </div>
        <div className="page-section-title"><h2>最近入库记录</h2><div className="ingestion-actions"><div className="ingestion-search"><Search size={13} /><input type="search" value={searchInput} onChange={(event) => setSearchInput(event.target.value)} placeholder="搜索频道、媒体、链接或原文" aria-label="搜索入库记录" />{searchInput ? <button type="button" onClick={() => setSearchInput("")} aria-label="清除搜索"><X size={12} /></button> : null}</div><label className="ingestion-status-filter">状态<select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>{STATUS_FILTERS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><span>{records.length} 条</span><button type="button" className="button small" onClick={retryFailed} disabled={retrying}>{retrying ? <LoaderCircle size={14} className="spin" /> : <RefreshCw size={14} />}{retrying ? "提交中…" : "重试失败记录"}</button><button type="button" className="button small" onClick={reprocessIgnored} disabled={reprocessing}>{reprocessing ? <LoaderCircle size={14} className="spin" /> : null}{reprocessing ? "识别中…" : "重新识别未识别媒体"}</button>{reprocessNotice ? <span className={`ingestion-reprocess-notice ingestion-reprocess-notice--${reprocessNotice.tone}`} role="status" aria-live="polite">{reprocessNotice.tone === "progress" ? <LoaderCircle size={13} className="spin" /> : reprocessNotice.tone === "error" ? <X size={13} /> : <CheckCircle2 size={13} />}{reprocessNotice.text}</span> : null}</div></div>
        <div className="history-table">
          <div className="history-head ingestion-head"><span>消息来源频道</span><span>来源渠道</span><span>入库媒体</span><span>状态</span><span>时间</span></div>
          {records.map((item) => (
            <button className="history-row history-row-button" type="button" key={item.id} onClick={() => openRecord(item)} aria-label={`查看 ${item.source_channel} 的消息明细`}>
              <span className="ingestion-channel">
                <span className="telegram-channel-avatar">{item.channel_avatar_url ? <img src={item.channel_avatar_url} alt="" loading="lazy" onError={(event) => { event.currentTarget.style.display = "none"; event.currentTarget.nextElementSibling.style.display = "grid"; }} /> : null}<span className={`telegram-channel-fallback ${item.channel_avatar_url ? "has-image" : ""}`}><Send size={16} fill="currentColor" /></span></span>
                <strong>{item.source_channel}</strong>
              </span>
              <span><TelegramIcon size={15} />{SOURCE_LABELS[item.source_service] || item.source_service}</span>
              <span className="ingestion-media" title={item.media_titles?.join("、")}>{item.media_titles?.length ? item.media_titles.join("、") : "未识别媒体"}</span>
              <span className="ingestion-state">
                <span className={`ingestion-status ingestion-status--${item.status}`}>{INGESTION_STATUS_LABELS[item.status] || item.status}</span>
                {item.error ? <small className="ingestion-status-reason" title={item.error}>{item.error}</small> : null}
              </span>
              <span>{formatDateTime(item.received_at)}</span>
            </button>
          ))}
          {!records.length ? <div className="history-empty">{search || statusFilter !== "all" ? "没有符合条件的入库记录" : "尚无入库记录"}</div> : null}
        </div>
      </section>

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
              <div>
                <span>入库消息明细</span>
                <h2 id="record-detail-title">{selectedRecord.channel_name || selectedRecord.channel_id || "未知频道"}</h2>
              </div>
              <button type="button" className="icon-only" onClick={() => setSelectedRecord(null)} aria-label="关闭消息明细"><X size={17} /></button>
            </header>
            <div className="record-detail-meta">
              <span>{SOURCE_LABELS[selectedRecord.service] || selectedRecord.service || "未知渠道"}</span>
              <span>{selectedRecord.message_url || `消息 ID：${selectedRecord.message_id || "未知"}`}</span>
              <span className={`ingestion-status ingestion-status--${selectedRecord.status}`}>{INGESTION_STATUS_LABELS[selectedRecord.status] || selectedRecord.status}</span>
            </div>
            {selectedRecord.error ? (
              <div className="record-detail-reason" role="status">
                <X size={14} />
                <span><strong>{INGESTION_STATUS_LABELS[selectedRecord.status] || "处理失败"}</strong>：{selectedRecord.error}</span>
              </div>
            ) : null}
            <div className="record-detail-section">
              <div className="record-detail-label"><strong>原始消息</strong><button type="button" className="button small" onClick={copyMessage} disabled={!selectedRecord.raw_text}>{copied ? <ClipboardCheck size={14} /> : <Clipboard size={14} />}{copied ? "已复制" : "复制"}</button></div>
              <pre className="record-detail-message">{selectedRecord.raw_text || "（无原始消息）"}</pre>
            </div>
            <div className="record-detail-section">
              <div className="record-detail-label"><strong>识别来源</strong><div className="record-detail-label-actions"><span>{selectedRecord.links?.length || 0} 条</span>{selectedRecord.links?.some((link) => RETRYABLE_LINK_STATUSES.includes(link.status)) ? <button type="button" className="button small" onClick={retryRecord} disabled={retryingRecord}>{retryingRecord ? <LoaderCircle size={14} className="spin" /> : <RefreshCw size={14} />}{retryingRecord ? "提交中…" : "重新校验并入库"}</button> : null}</div></div>
              {selectedRecord.links?.length ? (
                <div className="record-detail-links">
                  {selectedRecord.links.map((link) => <div key={link.id || link.source_key}><span>{link.provider} · {LINK_STATUS_LABELS[link.status] || link.status}</span><code>{link.url}</code>{link.validation_error ? <small>{link.validation_error}</small> : null}</div>)}
                </div>
              ) : <p className="record-detail-empty">没有提取到 115、123、光鸭或 ED2K 来源。</p>}
            </div>
            <details className="record-detail-raw">
              <summary>查看接收 JSON</summary>
              <pre>{JSON.stringify(selectedRecord.raw_json, null, 2)}</pre>
            </details>
          </section>
        </div>
      ) : null}

      {manualOpen ? (
        <div className="manual-import-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) onCloseManual(); }}>
          <section className="manual-import-modal" role="dialog" aria-modal="true" aria-labelledby="manual-import-title">
            <div className="manual-import-head">
              <div><span>手动入库</span><h2 id="manual-import-title">导入文本清单</h2></div>
              <button type="button" className="icon-only" onClick={onCloseManual} disabled={busy} aria-label="关闭手动入库">×</button>
            </div>
            <div
              className={`import-zone ${dragging ? "dragging" : ""}`}
              onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
              onDragLeave={() => setDragging(false)}
              onDrop={(event) => { event.preventDefault(); setDragging(false); handleFiles(event.dataTransfer.files); }}
            >
              {busy ? <LoaderCircle size={30} className="spin" /> : <UploadCloud size={30} />}
              <h2>{busy ? "正在解析并写入数据库" : "115、123、光鸭或 ED2K 文本清单"}</h2>
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
