import { useCallback, useEffect, useRef, useState } from "react";
import { CheckCircle2, Clipboard, ClipboardCheck, FolderInput, LoaderCircle, Radio, Send, UploadCloud, X } from "lucide-react";
import { getIngestionRecord, getIngestionRecords, importText, reprocessIgnoredIngestion } from "../api";
import { formatDateTime, formatNumber } from "../lib/format";

const SOURCE_LABELS = { telegram: "Telegram", discord: "Discord", webhook: "Webhook" };


export default function ImportView({ onImported, manualOpen, onCloseManual }) {
  const [records, setRecords] = useState([]);
  const [todayCount, setTodayCount] = useState(0);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [selectedRecord, setSelectedRecord] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [copied, setCopied] = useState(false);
  const [reprocessing, setReprocessing] = useState(false);
  const inputRef = useRef(null);

  const refresh = useCallback(() => {
    getIngestionRecords().then((payload) => {
      setRecords(payload.items || []);
      setTodayCount(payload.today_count || 0);
    }).catch((nextError) => setError(nextError.message));
  }, []);

  useEffect(refresh, [refresh]);

  const reprocessIgnored = async () => {
    setReprocessing(true);
    setError("");
    try {
      const result = await reprocessIgnoredIngestion(100);
      setResult({ source_name: "重新识别", total: result.queued, inserted: 0, duplicates: 0, errors: 0 });
      refresh();
    } catch (nextError) {
      setError(nextError.message);
    } finally {
      setReprocessing(false);
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
          <div><span>显示记录</span><strong>{formatNumber(records.length)}</strong><small>最近 100 条</small></div>
        </div>
        <div className="page-section-title"><h2>最近入库记录</h2><div><span>{records.length} 条</span><button type="button" className="button small" onClick={reprocessIgnored} disabled={reprocessing}>{reprocessing ? "识别中…" : "重新识别未识别媒体"}</button></div></div>
        <div className="history-table">
          <div className="history-head ingestion-head"><span>消息来源频道</span><span>来源渠道</span><span>入库媒体</span><span>状态</span><span>时间</span></div>
          {records.map((item) => (
            <button className="history-row history-row-button" type="button" key={item.id} onClick={() => openRecord(item)} aria-label={`查看 ${item.source_channel} 的消息明细`}>
              <span className="ingestion-channel">
                <span className="telegram-channel-avatar">{item.channel_avatar_url ? <img src={item.channel_avatar_url} alt="" loading="lazy" onError={(event) => { event.currentTarget.style.display = "none"; event.currentTarget.nextElementSibling.style.display = "grid"; }} /> : null}<span className="telegram-channel-fallback"><Send size={16} fill="currentColor" /></span></span>
                <strong>{item.source_channel}</strong>
              </span>
              <span><Radio size={14} />{SOURCE_LABELS[item.source_service] || item.source_service}</span>
              <span className="ingestion-media" title={item.media_titles?.join("、")}>{item.media_titles?.length ? item.media_titles.join("、") : "未识别媒体"}</span>
              <span className={`ingestion-status ingestion-status--${item.status}`}>{item.status === "imported" ? "已入库" : item.status === "duplicate" ? "重复" : item.status === "ignored" ? "已忽略" : item.status === "failed" ? "失败" : item.status}</span>
              <span>{formatDateTime(item.received_at)}</span>
            </button>
          ))}
          {!records.length ? <div className="history-empty">尚无入库记录</div> : null}
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
              <span className={`ingestion-status ingestion-status--${selectedRecord.status}`}>{selectedRecord.status === "imported" ? "已入库" : selectedRecord.status === "duplicate" ? "重复" : selectedRecord.status === "ignored" ? "已忽略" : selectedRecord.status === "failed" ? "失败" : selectedRecord.status}</span>
            </div>
            <div className="record-detail-section">
              <div className="record-detail-label"><strong>原始消息</strong><button type="button" className="button small" onClick={copyMessage} disabled={!selectedRecord.raw_text}>{copied ? <ClipboardCheck size={14} /> : <Clipboard size={14} />}{copied ? "已复制" : "复制"}</button></div>
              <pre className="record-detail-message">{selectedRecord.raw_text || "（无原始消息）"}</pre>
            </div>
            <div className="record-detail-section">
              <div className="record-detail-label"><strong>识别来源</strong><span>{selectedRecord.links?.length || 0} 条</span></div>
              {selectedRecord.links?.length ? (
                <div className="record-detail-links">
                  {selectedRecord.links.map((link) => <div key={link.id || link.source_key}><span>{link.provider} · {link.status}</span><code>{link.url}</code></div>)}
                </div>
              ) : <p className="record-detail-empty">没有提取到 115 或 ED2K 来源。</p>}
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
              <h2>{busy ? "正在解析并写入数据库" : "115 或 ED2K 文本清单"}</h2>
              <p>支持 UTF-8 文本，自动识别 115 分享链接及一行多个 ED2K 链接。</p>
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
    </main>
  );
}
