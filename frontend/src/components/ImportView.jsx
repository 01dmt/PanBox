import { useCallback, useEffect, useRef, useState } from "react";
import { CheckCircle2, FolderInput, LoaderCircle, MessageCircle, Radio, UploadCloud } from "lucide-react";
import { getIngestionRecords, importText } from "../api";
import { formatDateTime, formatNumber } from "../lib/format";

const SOURCE_LABELS = { telegram: "Telegram", discord: "Discord", webhook: "Webhook" };


export default function ImportView({ onImported, manualOpen, onCloseManual }) {
  const [records, setRecords] = useState([]);
  const [todayCount, setTodayCount] = useState(0);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const inputRef = useRef(null);

  const refresh = useCallback(() => {
    getIngestionRecords().then((payload) => {
      setRecords(payload.items || []);
      setTodayCount(payload.today_count || 0);
    }).catch((nextError) => setError(nextError.message));
  }, []);

  useEffect(refresh, [refresh]);

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

  return (
    <main className="page-view import-view">
      <section className="import-history">
        <div className="ingestion-summary">
          <div><span>今日入库数量</span><strong>{formatNumber(todayCount)}</strong><small>条消息</small></div>
          <div><span>显示记录</span><strong>{formatNumber(records.length)}</strong><small>最近 100 条</small></div>
        </div>
        <div className="page-section-title"><h2>最近入库记录</h2><span>{records.length} 条</span></div>
        <div className="history-table">
          <div className="history-head ingestion-head"><span>消息来源频道</span><span>来源渠道</span><span>入库媒体</span><span>状态</span><span>时间</span></div>
          {records.map((item) => (
            <div className="history-row" key={item.id}>
              <span className="ingestion-channel">
                {item.channel_avatar_url ? <img src={item.channel_avatar_url} alt="" loading="lazy" /> : <MessageCircle size={16} />}
                <strong>{item.source_channel}</strong>
              </span>
              <span><Radio size={14} />{SOURCE_LABELS[item.source_service] || item.source_service}</span>
              <span className="ingestion-media" title={item.media_titles?.join("、")}>{item.media_titles?.length ? item.media_titles.join("、") : "未识别媒体"}</span>
              <span className={`ingestion-status ingestion-status--${item.status}`}>{item.status === "imported" ? "已入库" : item.status === "duplicate" ? "重复" : item.status === "ignored" ? "已忽略" : item.status === "failed" ? "失败" : item.status}</span>
              <span>{formatDateTime(item.received_at)}</span>
            </div>
          ))}
          {!records.length ? <div className="history-empty">尚无入库记录</div> : null}
        </div>
      </section>

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
