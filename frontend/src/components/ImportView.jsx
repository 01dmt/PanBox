import { useCallback, useEffect, useRef, useState } from "react";
import { CheckCircle2, FileText, FolderInput, LoaderCircle, UploadCloud } from "lucide-react";
import { getImports, importText } from "../api";
import { formatDate, formatNumber } from "../lib/format";


export default function ImportView({ onImported }) {
  const [imports, setImports] = useState([]);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const inputRef = useRef(null);

  const refresh = useCallback(() => {
    getImports().then((payload) => setImports(payload.items)).catch((nextError) => setError(nextError.message));
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
    } catch (nextError) {
      setError(nextError.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="page-view import-view">
      <section className="import-zone-wrap">
        <div
          className={`import-zone ${dragging ? "dragging" : ""}`}
          onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => { event.preventDefault(); setDragging(false); handleFiles(event.dataTransfer.files); }}
        >
          {busy ? <LoaderCircle size={34} className="spin" /> : <UploadCloud size={34} />}
          <h2>{busy ? "正在解析并写入数据库" : "导入 115 或 ED2K 文本清单"}</h2>
          <p>支持 UTF-8 文本，自动识别 115 分享链接及一行多个 ED2K 链接。</p>
          <button type="button" className="button primary" onClick={() => inputRef.current?.click()} disabled={busy}>
            <FolderInput size={16} />选择文本文件
          </button>
          <input ref={inputRef} type="file" accept=".txt,text/plain" hidden onChange={(event) => handleFiles(event.target.files)} />
        </div>

        {error ? <div className="inline-error">{error}</div> : null}
        {result ? (
          <div className="import-result">
            <CheckCircle2 size={22} />
            <div><strong>{result.source_name}</strong><span>识别 {formatNumber(result.total)} · 新增 {formatNumber(result.inserted)} · 重复 {formatNumber(result.duplicates)} · 错误 {formatNumber(result.errors)}</span></div>
          </div>
        ) : null}
      </section>

      <section className="import-history">
        <div className="page-section-title"><h2>最近导入</h2><span>{imports.length} 次</span></div>
        <div className="history-table">
          <div className="history-head"><span>文件</span><span>类型</span><span>识别</span><span>新增</span><span>重复</span><span>时间</span></div>
          {imports.map((item) => (
            <div className="history-row" key={item.id}>
              <span><FileText size={16} /><strong>{item.source_name}</strong></span>
              <span>{item.source_kind.toUpperCase()}</span>
              <span>{formatNumber(item.total_records)}</span>
              <span>{formatNumber(item.inserted_records)}</span>
              <span>{formatNumber(item.duplicate_records)}</span>
              <span>{formatDate(item.created_at)}</span>
            </div>
          ))}
          {!imports.length ? <div className="history-empty">尚无导入记录</div> : null}
        </div>
      </section>
    </main>
  );
}

