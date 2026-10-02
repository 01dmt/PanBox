import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  Check,
  ChevronDown,
  Clipboard,
  ExternalLink,
  FileVideo2,
  Link2,
  LoaderCircle,
  Pencil,
  RefreshCw,
  Search,
  Unlink,
  X,
} from "lucide-react";
import {
  displayMediaType,
  displayResourceKind,
  displayStatus,
  formatBytes,
  maskedSource,
  posterUrl,
} from "../lib/format";
import TmdbSearch from "./TmdbSearch";


function parseJson(value, fallback = []) {
  if (!value) return fallback;
  try {
    return JSON.parse(value);
  } catch {
    return fallback;
  }
}


function SourceRow({ source, readOnly = false }) {
  const [copied, setCopied] = useState(false);
  const metadata = parseJson(source.metadata_json, {});
  const snapshot = metadata?.share_snapshot;
  const searchNames = snapshot?.status === "ok" ? snapshot.search_names || [] : [];
  const copy = async () => {
    await navigator.clipboard.writeText(source.url);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1400);
  };
  const seasonStart = source.season != null ? Number(source.season) : null;
  const seasonEnd = source.season_end != null ? Number(source.season_end) : null;
  const seasonLabel = seasonStart != null
    ? `S${String(seasonStart).padStart(2, "0")}${seasonEnd > seasonStart ? `-S${String(seasonEnd).padStart(2, "0")}` : ""}`
    : "";

  return (
    <div className="source-row">
      <span className={`source-icon source-${source.source_type}`}>
        {source.source_type === "115" ? <Link2 size={16} /> : <FileVideo2 size={16} />}
      </span>
      <div>
        <strong>{source.filename || source.raw_label}</strong>
        <span>
          {source.source_type}
          {source.file_size ? ` · ${formatBytes(source.file_size)}` : ""}
          {source.season != null ? ` · ${seasonLabel}${source.episode != null ? `E${String(source.episode).padStart(2, "0")}` : " · 整季"}` : ""}
        </span>
        <small>{source.source_type === "115" ? maskedSource(source.url) : source.ed2k_hash}</small>
        {searchNames.length ? <small className="source-match-hint">内容：{searchNames.slice(0, 2).join(" · ")}</small> : null}
        {snapshot?.status === "unavailable" ? <small className="source-unavailable">分享内容不可访问</small> : null}
      </div>
      {!readOnly && <div className="source-actions">
      {source.source_type === "115" ? (
        <button type="button" className="icon-only" onClick={() => window.open(source.url, "_blank", "noopener,noreferrer")} title="打开 115 链接">
          <ExternalLink size={15} />
        </button>
      ) : (
        <button type="button" className="icon-only" onClick={copy} title="复制 ED2K 链接">
          {copied ? <Check size={15} /> : <Clipboard size={15} />}
        </button>
      )}</div>}
    </div>
  );
}


function EpisodeResourceGroup({ sources }) {
  const seasons = useMemo(() => {
    const groups = new Map();
    for (const source of sources || []) {
      const numericSeason = source.season != null && !Number.isNaN(Number(source.season)) ? Number(source.season) : null;
      const season = Number.isInteger(numericSeason) ? numericSeason : null;
      const numericSeasonEnd = source.season_end != null && !Number.isNaN(Number(source.season_end)) ? Number(source.season_end) : null;
      const seasonEnd = Number.isInteger(numericSeasonEnd) && numericSeasonEnd > (season ?? 0) ? numericSeasonEnd : null;
      const key = season === null ? "unknown" : `${season}-${seasonEnd || season}`;
      if (!groups.has(key)) groups.set(key, { season, seasonEnd, packs: [], episodes: [] });
      const group = groups.get(key);
      if (source.episode == null) group.packs.push(source);
      else group.episodes.push(source);
    }
    return [...groups.values()].sort((a, b) => (a.season ?? Infinity) - (b.season ?? Infinity));
  }, [sources]);
  const [active, setActive] = useState("0");
  const group = seasons[Number(active)] || seasons[0];
  useEffect(() => { if (seasons.length && Number(active) >= seasons.length) setActive("0"); }, [active, seasons.length]);
  if (!seasons.length) return null;
  return <div className="season-resources">
    <div className="season-tabs" role="tablist" aria-label="选择季度">
      {seasons.map((season, index) => <button key={String(season.season)} type="button" role="tab" aria-selected={String(index) === active} className={String(index) === active ? "active" : ""} onClick={() => setActive(String(index))}>
        {season.season === null ? "未分类" : `第 ${season.season}${season.seasonEnd ? `-${season.seasonEnd}` : ""} 季`} <small>{season.packs.length ? `${season.packs.length} 整季` : ""}{season.packs.length && season.episodes.length ? " · " : ""}{season.episodes.length ? `${season.episodes.length} 集` : ""}</small>
      </button>)}
    </div>
    {group?.packs.length ? <div className="season-resource-block"><h4>整季资源</h4>{group.packs.map((source) => <SourceRow source={source} key={source.id} />)}</div> : null}
    {group?.episodes.length ? <div className="season-resource-block"><h4>单集资源</h4>{[...group.episodes].sort((a, b) => (a.episode ?? 0) - (b.episode ?? 0)).map((source) => <SourceRow source={source} key={source.id} />)}</div> : null}
    {!group?.packs.length && !group?.episodes.length ? <p className="source-empty">该季暂无资源</p> : null}
  </div>;
}


function Candidate({ candidate, onLink, onReject, disabled }) {
  return (
    <div className="candidate-row">
      <img src={posterUrl(candidate.poster_path, "w185")} alt="" loading="lazy" />
      <div>
        <strong>{candidate.title}</strong>
        <span>{candidate.release_date?.slice(0, 4) || "年份未知"} · {displayMediaType(candidate.media_type)}</span>
        <small>TMDB #{candidate.tmdb_id}</small>
      </div>
      <b className={candidate.score >= 0.85 ? "score-high" : candidate.score >= 0.65 ? "score-mid" : "score-low"}>
        {Math.round(candidate.score * 100)}%
      </b>
      <button type="button" className="button small primary" onClick={() => onLink(candidate)} disabled={disabled}>关联</button>
      <button type="button" className="icon-only reject" onClick={() => onReject(candidate.id)} disabled={disabled} title="排除此候选"><X size={15} /></button>
    </div>
  );
}


const IMDB_STATUSES = {
  missing_year: "缺少本地年份",
  conflicting_years: "来源年份冲突",
  collection: "系列或合集待确认",
  no_exact_match: "没有片名、年份完全一致的 IMDb 结果",
  ambiguous_imdb: "IMDb 存在多个同名同年条目",
  no_tmdb_mapping: "IMDb 已定位，TMDB 没有对应关联",
  ambiguous_tmdb: "IMDb ID 对应多个 TMDB 条目",
  type_conflict: "IMDb 与 TMDB 类型不一致",
  external_id_mismatch: "IMDb ID 回核不一致",
  tmdb_year_conflict: "IMDb 与 TMDB 年份不一致",
  tmdb_missing_year: "TMDB 缺少年份",
  located: "IMDb ID 已定位 TMDB",
  located_imdb_year: "已核验，采用 IMDb 年份",
  review: "已定位，仍需确认",
  error: "IMDb 辅助检索失败",
};


function ImdbEvidence({ lookup }) {
  if (!lookup) return null;
  return (
    <section className="inspector-section imdb-evidence" aria-label="IMDb 核验" data-status={lookup.status}>
      <div className="section-title"><h3>IMDb 核验</h3></div>
      <strong>{IMDB_STATUSES[lookup.status] || lookup.status}</strong>
      <p>{lookup.year_override ? "采用年份（IMDb）" : "核验年份"}：{lookup.expected_year ?? "未提供"}</p>
      {lookup.error ? <p role="status">{lookup.error}</p> : null}
      {lookup.matches?.map((match) => (
        <div className="imdb-proof-row" key={match.imdb_id}>
          <span>{match.title} · {match.year} · {displayMediaType(match.media_type)}</span>
          <a href={`https://www.imdb.com/title/${encodeURIComponent(match.imdb_id)}/`} target="_blank" rel="noopener noreferrer">
            {match.imdb_id}<ExternalLink size={12} aria-hidden="true" />
          </a>
        </div>
      ))}
      {lookup.mapped?.map((match) => (
        <div className="imdb-proof-row" key={`${match.media_type}:${match.tmdb_id}`}>
          <span>TMDB：{match.title || match.tmdb_id} · {match.year ?? "年份未知"}</span>
          <a href={`https://www.themoviedb.org/${match.media_type}/${match.tmdb_id}`} target="_blank" rel="noopener noreferrer">
            {match.media_type}/{match.tmdb_id}<ExternalLink size={12} aria-hidden="true" />
          </a>
        </div>
      ))}
    </section>
  );
}


export default function Inspector({
  readOnly = false,
  item,
  sourceIds,
  loading,
  tmdbConfigured,
  mobileOpen,
  onClose,
  onSearch,
  onLink,
  onReject,
  onUnlink,
  onUpdate,
}) {
  const [editing, setEditing] = useState(false);
  const [showAllSources, setShowAllSources] = useState(false);
  const [tmdbSearchFor, setTmdbSearchFor] = useState(null);
  const [form, setForm] = useState({ title: "", year: "", media_type: "unknown" });
  const [manual, setManual] = useState({ tmdb_id: "", media_type: "movie" });
  const scrollRef = useRef(null);

  useLayoutEffect(() => {
    scrollRef.current?.scrollTo({ top: 0, left: 0 });
  }, [item?.id]);

  useEffect(() => {
    setForm({
      title: item?.title || "",
      year: item?.year || "",
      media_type: item?.media_type || "unknown",
    });
    setEditing(false);
    setShowAllSources(false);
  }, [item?.id]);

  const genres = useMemo(() => parseJson(item?.genres_json), [item?.genres_json]);
  const matchingSources = useMemo(() => {
    if (!sourceIds) return item?.sources;
    const allowed = new Set(sourceIds);
    return item?.sources?.filter((source) => allowed.has(source.id));
  }, [item?.sources, sourceIds]);
  const visibleSources = showAllSources ? matchingSources : matchingSources?.slice(0, 6);

  if (!item && !loading) {
    return (
      <aside className="inspector empty-inspector">
        <FileVideo2 size={28} />
        <strong>选择一条媒体记录</strong>
        <span>来源和 TMDB 候选会显示在这里。</span>
      </aside>
    );
  }

  return (
    <aside className={`inspector ${mobileOpen ? "mobile-open" : ""}`} aria-label="媒体详情">
      <div className="sheet-handle" />
      <div className="inspector-head">
        <div>
          <strong>{item?.tmdb_title || item?.title || "载入中"}</strong>
          <span>{item?.year || item?.release_date?.slice(0, 4) || "年份未知"} · {displayMediaType(item?.media_type)}</span>
        </div>
        <button type="button" className="icon-only mobile-close" onClick={onClose} aria-label="关闭详情"><X size={18} /></button>
      </div>

      {loading && !item ? (
        <div className="inspector-loading"><LoaderCircle className="spin" size={24} />载入详情</div>
      ) : null}

      {item ? (
        <div className="inspector-scroll" ref={scrollRef}>
          <section className="media-summary">
            <img src={posterUrl(item.poster_path)} alt="" />
            <div>
              <span className={`status-chip status-${item.tmdb_status}`}>{displayStatus(item.tmdb_status)}</span>
              <h2>{item.tmdb_title || item.title}</h2>
              {item.original_title ? <p>{item.original_title}</p> : null}
              <dl>
                <div><dt>年份</dt><dd>{item.year || item.release_date?.slice(0, 4) || "—"}</dd></div>
                <div><dt>资源分类</dt><dd>{displayResourceKind(item.resource_kind)}</dd></div>
                <div><dt>类型</dt><dd>{displayMediaType(item.media_type)}</dd></div>
                <div><dt>来源</dt><dd>{item.source_count} 条</dd></div>
                {item.episode_count ? <div><dt>剧集</dt><dd>{item.episode_count} 集 / {item.max_season || 1} 季</dd></div> : null}
                {item.tmdb_id ? <div><dt>TMDB</dt><dd>#{item.tmdb_id}</dd></div> : null}
              </dl>
              {item.resource_kind !== "media" && parseJson(item.resource_metadata_json, {}).resource_metadata ? <div className="genre-line resource-facts">{Object.entries(parseJson(item.resource_metadata_json, {}).resource_metadata).map(([key, value]) => <span key={key}>{value}</span>)}</div> : null}
              {genres.length ? <div className="genre-line">{genres.slice(0, 4).map((genre) => <span key={genre.id}>{genre.name}</span>)}</div> : null}
            </div>
          </section>

          {item.overview ? <p className="overview">{item.overview}</p> : null}

          <section className="inspector-section">
            <div className="section-title">
              <h3>本地元数据</h3>
              {!readOnly && <button type="button" className="text-command" onClick={() => setEditing((value) => !value)}>
                <Pencil size={14} />{editing ? "取消" : "编辑"}
              </button>}
            </div>
            {!readOnly && editing ? (
              <form
                className="edit-form"
                onSubmit={(event) => {
                  event.preventDefault();
                  onUpdate({ ...form, year: form.year ? Number(form.year) : null });
                  setEditing(false);
                }}
              >
                <label>标题<input value={form.title} onChange={(event) => setForm((current) => ({ ...current, title: event.target.value }))} /></label>
                <label>年份<input type="number" min="1880" max="2100" value={form.year} onChange={(event) => setForm((current) => ({ ...current, year: event.target.value }))} /></label>
                <label>类型<select value={form.media_type} onChange={(event) => setForm((current) => ({ ...current, media_type: event.target.value }))}><option value="unknown">待判断</option><option value="movie">电影</option><option value="tv">剧集</option></select></label>
                <button type="submit" className="button primary">保存</button>
              </form>
            ) : null}
          </section>

          <section className="inspector-section">
            <div className="section-title">
              <h3>来源记录 <span>{matchingSources?.length ?? 0}{matchingSources?.length < item.source_count ? ` / 共 ${item.source_count}` : ""}</span></h3>
            </div>
                    {item.media_type === "tv" ? <EpisodeResourceGroup key={item.id} sources={matchingSources} /> : <div className="source-list">
              {visibleSources?.map((source) => <SourceRow source={source} readOnly={readOnly} key={source.id} />)}
            </div>}
            {!matchingSources?.length ? <p className="source-empty">暂无符合条件的来源</p> : null}
            {item.media_type !== "tv" && matchingSources?.length > 6 ? (
              <button type="button" className="show-more" onClick={() => setShowAllSources((value) => !value)}>
                {showAllSources ? "收起来源" : `查看全部 ${matchingSources.length} 条来源`}<ChevronDown size={15} />
              </button>
            ) : null}
          </section>

          {!readOnly && <ImdbEvidence lookup={item.imdb_lookup} />}

          {!readOnly && <section className="inspector-section candidate-section">
            <div className="section-title">
              <h3>TMDB 匹配候选</h3>
              <div className="candidate-actions">
                {item.tmdb_status !== "matched" ? <button type="button" className="icon-only" onClick={() => setTmdbSearchFor((current) => current === item.id ? null : item.id)} disabled={!tmdbConfigured || loading} title="手动搜索 TMDB" aria-label="手动搜索 TMDB" aria-expanded={tmdbSearchFor === item.id} aria-controls="manual-tmdb-search"><Search size={16} /></button> : null}
                <button type="button" className="icon-only" onClick={onSearch} disabled={!tmdbConfigured || loading} title="重新刮削候选" aria-label="重新刮削候选">
                  <RefreshCw size={16} className={loading ? "spin" : ""} />
                </button>
              </div>
            </div>

            {item.tmdb_status !== "matched" && tmdbSearchFor === item.id ? <div id="manual-tmdb-search"><TmdbSearch key={item.id} title={item.title} disabled={!tmdbConfigured || loading} onLink={onLink} /></div> : null}

            {item.tmdb_status === "matched" ? (
              <div className="matched-panel">
                <Check size={18} />
                <div><strong>已关联 TMDB #{item.tmdb_id}</strong><span>匹配方式：{item.match_method === "auto" ? "自动" : "人工"}</span></div>
                <button type="button" className="button small" onClick={onUnlink}><Unlink size={14} />解除</button>
              </div>
            ) : null}

            {!item.candidates?.length && item.tmdb_status !== "matched" && tmdbSearchFor !== item.id ? (
              <div className="candidate-empty">
                <Search size={22} />
                <span>{tmdbConfigured ? "搜索 TMDB 生成候选结果" : "配置 TMDB 凭据后可搜索候选"}</span>
                <button type="button" className="button primary" onClick={onSearch} disabled={!tmdbConfigured || loading}>搜索候选</button>
              </div>
            ) : null}

            {tmdbSearchFor === item.id && item.candidates?.length ? <h4 className="saved-candidates-heading">已有候选</h4> : null}
            <div className="candidate-list">
              {item.candidates?.map((candidate) => (
                <Candidate key={candidate.id} candidate={candidate} onLink={onLink} onReject={onReject} disabled={loading} />
              ))}
            </div>

            {item.tmdb_status !== "matched" ? (
              <form
                className="manual-link"
                onSubmit={(event) => {
                  event.preventDefault();
                  if (!manual.tmdb_id) return;
                  onLink({
                    tmdb_id: Number(manual.tmdb_id),
                    media_type: manual.media_type,
                    score: 1,
                  });
                }}
              >
                <input type="number" min="1" placeholder="TMDB ID" value={manual.tmdb_id} onChange={(event) => setManual((current) => ({ ...current, tmdb_id: event.target.value }))} />
                <select value={manual.media_type} onChange={(event) => setManual((current) => ({ ...current, media_type: event.target.value }))}><option value="movie">电影</option><option value="tv">剧集</option></select>
                <button type="submit" className="button" disabled={!tmdbConfigured || loading}>手动关联</button>
              </form>
            ) : null}
          </section>}
        </div>
      ) : null}
    </aside>
  );
}
