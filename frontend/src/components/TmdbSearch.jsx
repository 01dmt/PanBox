import { useEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, ExternalLink, LoaderCircle, Search } from "lucide-react";
import { searchTmdb } from "../api";
import { displayMediaType, posterUrl } from "../lib/format";


export default function TmdbSearch({ title, disabled, onLink }) {
  const [query, setQuery] = useState(title);
  const [type, setType] = useState("multi");
  const [includeAdult, setIncludeAdult] = useState(false);
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const controllerRef = useRef(null);
  const inputRef = useRef(null);
  const resultsRef = useRef(null);

  useEffect(() => {
    inputRef.current?.focus({ preventScroll: true });
    return () => controllerRef.current?.abort();
  }, []);

  const resetResults = () => {
    controllerRef.current?.abort();
    setBusy(false);
    setResult(null);
    setError("");
  };

  const search = async (page = 1) => {
    if (!query.trim() || disabled) return;
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setBusy(true);
    setError("");
    try {
      const next = await searchTmdb({ q: query.trim(), type, page, include_adult: includeAdult }, controller.signal);
      if (controller.signal.aborted) return;
      setResult(next);
      resultsRef.current?.scrollTo({ top: 0 });
    } catch (nextError) {
      if (!controller.signal.aborted) setError(nextError.message);
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  };

  return (
    <div className="tmdb-search" aria-label="手动搜索 TMDB">
      <form onSubmit={(event) => { event.preventDefault(); search(); }}>
        <div className="tmdb-search-query">
          <input ref={inputRef} aria-label="TMDB 搜索词" placeholder="片名或原名" maxLength={200} value={query} disabled={disabled} onChange={(event) => { resetResults(); setQuery(event.target.value); }} />
          <button type="submit" className="button primary" disabled={disabled || busy || !query.trim()} title="搜索 TMDB" aria-label="搜索 TMDB">
            {busy ? <LoaderCircle size={17} className="spin" aria-hidden="true" /> : <Search size={17} aria-hidden="true" />}
          </button>
          <a className="icon-only tmdb-website" href={`https://www.themoviedb.org/search?query=${encodeURIComponent(query.trim())}`} target="_blank" rel="noopener noreferrer" title="在 TMDB 官网搜索" aria-label="在 TMDB 官网搜索"><ExternalLink size={16} aria-hidden="true" /></a>
        </div>
        <div className="tmdb-search-options">
          <select aria-label="TMDB 搜索类型" value={type} disabled={disabled} onChange={(event) => { resetResults(); setType(event.target.value); }}>
            <option value="multi">电影和剧集</option><option value="movie">电影</option><option value="tv">剧集</option>
          </select>
          <label><input type="checkbox" checked={includeAdult} disabled={disabled} onChange={(event) => { resetResults(); setIncludeAdult(event.target.checked); }} />包含成人内容</label>
        </div>
      </form>
      {error ? <p className="tmdb-search-error" role="alert">{error}</p> : null}
      {busy ? <p className="tmdb-search-state" role="status">正在搜索 TMDB…</p> : null}
      {result ? (
        <>
          <div className="tmdb-result-summary">
            <span>本页 {result.results.length} 条</span>
            {result.excluded_undated ? <span>已排除 {result.excluded_undated} 条无年份结果</span> : null}
          </div>
          <div className="tmdb-search-results" ref={resultsRef} role="region" tabIndex={0} aria-label="TMDB 搜索结果" aria-busy={busy}>
            {!result.results.length ? <p className="tmdb-search-state">本页没有可关联结果</p> : null}
            {result.results.map((candidate) => (
              <div className="tmdb-search-result" key={`${candidate.media_type}:${candidate.tmdb_id}`}>
                <img src={posterUrl(candidate.poster_path, "w185")} alt="" loading="lazy" />
                <div className="tmdb-result-info">
                  <a href={`https://www.themoviedb.org/${candidate.media_type}/${candidate.tmdb_id}`} target="_blank" rel="noopener noreferrer" title="在 TMDB 查看条目">{candidate.title}<ExternalLink size={11} aria-hidden="true" /></a>
                  {candidate.original_title && candidate.original_title !== candidate.title ? <span>{candidate.original_title}</span> : null}
                  <small>{candidate.release_date?.slice(0, 4)} · {displayMediaType(candidate.media_type)} · #{candidate.tmdb_id}</small>
                </div>
                <button type="button" className="button small" disabled={disabled || busy || Boolean(error)} onClick={() => onLink(candidate)} aria-label={`关联 ${candidate.title}，${candidate.media_type}/${candidate.tmdb_id}`}>关联</button>
                {candidate.overview ? <details className="tmdb-result-overview"><summary>简介</summary><p>{candidate.overview}</p></details> : null}
              </div>
            ))}
          </div>
          <div className="tmdb-search-pagination">
            <button type="button" className="icon-only" disabled={disabled || busy || result.page <= 1} onClick={() => search(result.page - 1)} aria-label="TMDB 上一页" title="上一页"><ChevronLeft size={16} /></button>
            <span>第 {result.page} / {result.pages} 页</span>
            <button type="button" className="icon-only" disabled={disabled || busy || result.page >= result.pages} onClick={() => search(result.page + 1)} aria-label="TMDB 下一页" title="下一页"><ChevronRight size={16} /></button>
          </div>
        </>
      ) : null}
    </div>
  );
}
