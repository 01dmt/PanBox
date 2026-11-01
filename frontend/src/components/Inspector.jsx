import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  Check,
  ChevronDown,
  Clipboard,
  ExternalLink,
  FileVideo2,
  Film,
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
  formatDate,
  formatDateTime,
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


function importMethodLabel(source) {
  const name = source.import_source_name || "";
  if (name.startsWith("webhook:")) return "Webhook 实时入库";
  if (name.startsWith("telegram:")) return "Telegram 导出导入";
  if (name === "browser-import.txt") return "网页手动导入";
  if (source.import_source_path) return "本地文件导入";
  return "批量导入";
}


function messagePreview(text) {
  return (text || "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .slice(0, 2)
    .join(" · ")
    .slice(0, 140);
}


function SourceRow({ source, readOnly = false, onHover }) {
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
  // 区间资源（如 E01-E25）存于 metadata.episode_range，展示完整范围而不只是起始集。
  const episodeRange = Array.isArray(metadata?.episode_range) && metadata.episode_range.length === 2 ? metadata.episode_range : null;
  const episodeLabel = source.episode != null
    ? episodeRange && Number(episodeRange[1]) > Number(source.episode)
      ? `E${String(source.episode).padStart(2, "0")}-E${String(Number(episodeRange[1])).padStart(2, "0")}`
      : `E${String(source.episode).padStart(2, "0")}`
    : "";
  const isCloudShare = ["115", "123", "guangya"].includes(source.source_type);

  return (
    <div className="source-row"
         onMouseEnter={onHover ? () => onHover(source.id) : undefined}
         onMouseLeave={onHover ? () => onHover(null) : undefined}>
      <span className={`source-icon source-${source.source_type}`}>
        {isCloudShare ? <Link2 size={16} /> : <FileVideo2 size={16} />}
      </span>
      <div>
        <strong>{source.filename || source.raw_label}</strong>
        <span>
          {source.source_type}
          {source.file_size ? ` · ${formatBytes(source.file_size)}` : ""}
          {source.season != null ? ` · ${seasonLabel}${source.episode != null ? episodeLabel : " · 整季"}` : ""}
        </span>
        <small>{isCloudShare ? maskedSource(source.url) : source.ed2k_hash}</small>
        <small className="source-origin" title={source.import_source_name || undefined}>
          {importMethodLabel(source)}
          {source.import_created_at ? ` · ${formatDateTime(source.import_created_at)}` : ""}
          {source.import_id ? ` · 批次 #${source.import_id}` : ""}
        </small>
        {searchNames.length ? <small className="source-match-hint">内容：{searchNames.slice(0, 2).join(" · ")}</small> : null}
        {snapshot?.status === "unavailable" ? <small className="source-unavailable">分享内容不可访问</small> : null}
      </div>
      {!readOnly && <div className="source-actions">
      {isCloudShare ? (
        <button type="button" className="icon-only" onClick={() => window.open(source.url, "_blank", "noopener,noreferrer")} title={`打开 ${source.provider || source.source_type} 链接`}>
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


function sourceCoversEpisodes(source) {
  // 返回该来源覆盖的集号集合：整季包返回 null（表示整季），区间/单集返回数字数组。
  if (source.episode == null) return null;
  const metadata = parseJson(source.metadata_json, {});
  const range = Array.isArray(metadata?.episode_range) && metadata.episode_range.length === 2 ? metadata.episode_range : null;
  const start = Number(source.episode);
  const end = range ? Math.max(Number(range[1]), start) : start;
  const episodes = [];
  for (let episode = start; episode <= end; episode += 1) episodes.push(episode);
  return episodes;
}


// 区间资源的横线颜色盘：重叠区间（如 1-5 与 2-7）用不同颜色区分不同链接。
const RANGE_COLORS = ["#7c5cfc", "#e0678b", "#2f9e8f", "#d9862b", "#4a7fd4", "#b0573f"];


function sourceCoverage(source) {
  // 整季包返回 episodes: null；单集/区间返回集号数组与类型（kind: single | range）。
  if (source.episode == null) return { episodes: null, kind: "pack" };
  const metadata = parseJson(source.metadata_json, {});
  const range = Array.isArray(metadata?.episode_range) && metadata.episode_range.length === 2 ? metadata.episode_range : null;
  const start = Number(source.episode);
  const end = range ? Math.max(Number(range[1]), start) : start;
  const episodes = [];
  for (let episode = start; episode <= end; episode += 1) episodes.push(episode);
  return { episodes, kind: end > start ? "range" : "single" };
}


function EpisodeCoverageGrid({ group, tmdbTotal, spotlight }) {
  // 集数格子：已有的高亮、缺的灰色；整季包按 TMDB 基准记为整季覆盖。
  // 标记：● 该集有专门单集资源；▬ 区间资源（按链接着色，重叠时堆叠）；无标记 = 仅整季包。
  // spotlight 为悬停中的来源 id：对应格子加描边，其余变暗。
  const { cells, coverageBySource } = useMemo(() => {
    const owned = new Map();
    const coverageBySource = new Map();
    let maxOwned = 0;
    let rangeIndex = 0;
    const mark = (episode, source, kind, colorIndex) => {
      maxOwned = Math.max(maxOwned, episode);
      if (!owned.has(episode)) owned.set(episode, { owners: [], kinds: new Set(), ranges: [] });
      const cell = owned.get(episode);
      cell.owners.push(source.filename || source.raw_label || source.source_type);
      cell.kinds.add(kind);
      if (kind === "range") cell.ranges.push(colorIndex);
    };
    for (const source of group.episodes) {
      const { episodes, kind } = sourceCoverage(source);
      const colorIndex = kind === "range" ? rangeIndex++ % RANGE_COLORS.length : null;
      const covered = new Set();
      for (const episode of episodes || []) {
        covered.add(episode);
        mark(episode, source, kind, colorIndex);
      }
      coverageBySource.set(source.id, covered);
    }
    const total = Math.max(tmdbTotal || 0, maxOwned);
    const packCovered = new Set(Array.from({ length: total }, (_, index) => index + 1));
    for (const source of group.packs) coverageBySource.set(source.id, packCovered);
    const cells = Array.from({ length: total }, (_, index) => {
      const episode = index + 1;
      const packOwned = group.packs.length > 0 && (tmdbTotal ? episode <= tmdbTotal : true);
      const cell = owned.get(episode);
      const owners = cell?.owners || [];
      const kinds = cell?.kinds || new Set();
      return { episode, owned: packOwned || owners.length > 0, owners, packOwned, single: kinds.has("single"), ranges: cell?.ranges || [] };
    });
    return { cells, coverageBySource };
  }, [group, tmdbTotal]);
  if (!cells.length) return null;
  const hasMarkers = cells.some((cell) => cell.single || cell.ranges.length);
  const spotlightSet = spotlight != null ? coverageBySource.get(spotlight) : null;
  return <>
    <div className="episode-grid" role="list" aria-label="集数覆盖">
      {cells.map((cell) => {
        const kindHint = cell.single ? "单集资源" : cell.ranges.length ? "区间资源" : cell.packOwned ? "整季包覆盖" : null;
        const hint = cell.owners.length
          ? `E${String(cell.episode).padStart(2, "0")} · ${kindHint} · ${cell.owners.slice(0, 2).join("、")}`
          : cell.packOwned
            ? `E${String(cell.episode).padStart(2, "0")} · 整季包覆盖`
            : `E${String(cell.episode).padStart(2, "0")} · 缺失`;
        const spotlighted = spotlightSet ? spotlightSet.has(cell.episode) : false;
        const className = `episode-cell ${cell.owned ? "owned" : "missing"}${spotlightSet ? (spotlighted ? " spotlight" : " dimmed") : ""}`;
        return <span key={cell.episode} role="listitem" className={className} title={hint}>
          {String(cell.episode).padStart(2, "0")}
          {cell.single ? <i className="episode-dot" aria-hidden="true" /> : null}
          {cell.ranges.map((colorIndex, stack) => (
            <i key={colorIndex} className="episode-range-mark" aria-hidden="true"
               style={{ background: RANGE_COLORS[colorIndex], bottom: `${(cell.single ? 6 : 2.5) + stack * 3}px` }} />
          ))}
        </span>;
      })}
    </div>
    {hasMarkers ? <p className="episode-legend"><span><i className="episode-dot" aria-hidden="true" /> 单集</span><span><i className="episode-range-mark" style={{ background: RANGE_COLORS[0] }} aria-hidden="true" /><i className="episode-range-mark" style={{ background: RANGE_COLORS[1] }} aria-hidden="true" /> 区间（颜色区分链接）</span><span>无标记 = 仅整季包</span></p> : null}
  </>;
}


function compressEpisodeRanges(numbers) {
  const sorted = [...new Set(numbers)].sort((a, b) => a - b);
  const ranges = [];
  let start = null;
  let prev = null;
  const flush = () => {
    if (start === null) return;
    ranges.push(start === prev
      ? `E${String(start).padStart(2, "0")}`
      : `E${String(start).padStart(2, "0")}-E${String(prev).padStart(2, "0")}`);
  };
  for (const episode of sorted) {
    if (start === null) { start = prev = episode; continue; }
    if (episode === prev + 1) { prev = episode; continue; }
    flush();
    start = prev = episode;
  }
  flush();
  return ranges.join(" ");
}


function EpisodeResourceGroup({ sources, seasonsJson }) {
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
  const [spotlight, setSpotlight] = useState(null);
  const group = seasons[Number(active)] || seasons[0];
  useEffect(() => { if (seasons.length && Number(active) >= seasons.length) setActive("0"); }, [active, seasons.length]);
  if (!seasons.length) return null;
  // TMDB 季集基准（seasons_json 在关联时缓存、详情页懒回填）；在播剧以 TMDB 当前值为准。
  const tmdbSeasons = parseJson(seasonsJson, []);
  const tmdbTotal = group && group.season != null
    ? (tmdbSeasons.find((row) => Number(row.season) === group.season)?.episode_count ?? null)
    : null;
  const ownedEpisodes = new Set();
  for (const source of group?.episodes || []) {
    for (const episode of sourceCoverage(source).episodes || []) ownedEpisodes.add(episode);
  }
  const packCount = group?.packs.length || 0;
  const ownedSummary = packCount && tmdbTotal
    ? `已拥有整季 ${tmdbTotal} 集`
    : ownedEpisodes.size
      ? `已拥有 ${ownedEpisodes.size} 集（${compressEpisodeRanges(ownedEpisodes)}）`
      : packCount ? "整季资源（TMDB 集数未知）" : "暂无集数资源";
  return <div className="season-resources">
    <div className="season-tabs" role="tablist" aria-label="选择季度">
      {seasons.map((season, index) => <button key={String(season.season)} type="button" role="tab" aria-selected={String(index) === active} className={String(index) === active ? "active" : ""} onClick={() => setActive(String(index))}>
        {season.season === null ? "未分类" : `第 ${season.season}${season.seasonEnd ? `-${season.seasonEnd}` : ""} 季`} <small>{season.packs.length ? `${season.packs.length} 整季` : ""}{season.packs.length && season.episodes.length ? " · " : ""}{season.episodes.length ? `${season.episodes.length} 集` : ""}</small>
      </button>)}
    </div>
    {group && group.season != null ? <>
      <p className="season-summary">
        {tmdbTotal ? `TMDB 共 ${tmdbTotal} 集` : "TMDB 集数未知"} · {ownedSummary}
        {packCount && ownedEpisodes.size ? ` · ${packCount} 个整季包` : ""}
      </p>
      <EpisodeCoverageGrid group={group} tmdbTotal={tmdbTotal} spotlight={spotlight} />
    </> : null}
    {group?.packs.length ? <div className="season-resource-block"><h4>整季资源</h4>{group.packs.map((source) => <SourceRow source={source} key={source.id} onHover={setSpotlight} />)}</div> : null}
    {group?.episodes.length ? <div className="season-resource-block"><h4>单集资源</h4>{[...group.episodes].sort((a, b) => (a.episode ?? 0) - (b.episode ?? 0)).map((source) => <SourceRow source={source} key={source.id} onHover={setSpotlight} />)}</div> : null}
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
  collection: "合集待确认",
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


function padEpisode(value) {
  const number = Number(value);
  return Number.isFinite(number) ? String(number).padStart(2, "0") : String(value);
}


function extractedEpisodeLabels(sources) {
  const labels = [];
  const seasonPacks = new Set();
  for (const source of sources || []) {
    if (source.episode == null) {
      if (source.season != null) seasonPacks.add(`S${padEpisode(source.season)} 整季`);
      continue;
    }
    const metadata = parseJson(source.metadata_json, {});
    const range = Array.isArray(metadata?.episode_range) && metadata.episode_range.length === 2
      ? metadata.episode_range
      : null;
    const start = Number(source.episode);
    const end = range ? Math.max(Number(range[1]), start) : start;
    const season = source.season != null ? Number(source.season) : 1;
    labels.push(`S${padEpisode(season)}E${padEpisode(start)}${end > start ? `-E${padEpisode(end)}` : ""}`);
  }
  const unique = [...new Set(labels)].sort();
  if (unique.length) {
    return `${unique.slice(0, 6).join("、")}${unique.length > 6 ? ` 等 ${unique.length} 个` : ""}`;
  }
  if (seasonPacks.size) return [...seasonPacks].sort().join("、");
  return "未提取";
}


function ParsedInfo({ item }) {
  const metadata = parseJson(item?.resource_metadata_json, {});
  const heading = String(metadata?.context || "").split("\n")[0].trim();
  const sourceNames = [...new Set((item?.sources || []).map((source) => source.raw_label || source.filename).filter(Boolean))].slice(0, 3);
  const year = item?.year || item?.release_date?.slice(0, 4) || "未提取";
  return (
    <section className="inspector-section parsed-info" aria-label="当前提取信息">
      <div className="section-title">
        <h3>当前提取信息</h3>
        <span>入库解析结果</span>
      </div>
      <dl className="parsed-fields">
        <div><dt>提取标题</dt><dd>{item?.title || "未提取"}</dd></div>
        <div><dt>年份</dt><dd>{year}</dd></div>
        <div><dt>类型</dt><dd>{displayMediaType(item?.media_type)}</dd></div>
        <div><dt>季 / 集</dt><dd>{extractedEpisodeLabels(item?.sources)}</dd></div>
        {heading ? <div><dt>频道标题</dt><dd>{heading}</dd></div> : null}
        {sourceNames.length ? (
          <div>
            <dt>资源名</dt>
            <dd>
              {sourceNames.join("；")}
              {(item?.sources?.length || 0) > sourceNames.length ? ` 等 ${item.sources.length} 条` : ""}
            </dd>
          </div>
        ) : null}
      </dl>
      <p className="parsed-hint">关联后标题以 TMDB 为准；这里展示的是当前用于判断的解析结果。</p>
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
            {item.poster_path ? <img src={posterUrl(item.poster_path)} alt="" /> : <div className="media-summary-poster placeholder" aria-hidden="true"><Film size={22} /><span>暂无海报</span></div>}
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

          <ParsedInfo item={item} />

          {/* 待匹配工作流：候选是主操作，放在摘要之后，避免用户滚动整个详情才能关联。 */}
          {!readOnly && <section className="inspector-section candidate-section priority">
            <div className="section-title">
              <h3>TMDB 匹配候选 {item.candidates?.length ? <span>{item.candidates.length}</span> : null}</h3>
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
                <div><strong>已关联 TMDB #{item.tmdb_id}</strong><span>匹配方式：{item.match_method === "auto" ? "自动" : item.match_method === "message_evidence" ? "消息确认" : "人工"}</span></div>
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
                    {item.media_type === "tv" ? <EpisodeResourceGroup key={item.id} sources={matchingSources} seasonsJson={item.seasons_json} /> : <div className="source-list">
              {visibleSources?.map((source) => <SourceRow source={source} readOnly={readOnly} key={source.id} />)}
            </div>}
            {!matchingSources?.length ? <p className="source-empty">暂无符合条件的来源</p> : null}
            {item.media_type !== "tv" && matchingSources?.length > 6 ? (
              <button type="button" className="show-more" onClick={() => setShowAllSources((value) => !value)}>
                {showAllSources ? "收起来源" : `查看全部 ${matchingSources.length} 条来源`}<ChevronDown size={15} />
              </button>
            ) : null}
          </section>

          {item.original_messages?.length ? (
            <section className="inspector-section">
              <div className="section-title">
                <h3>消息来源 <span>{item.original_messages.length}</span></h3>
              </div>
              <div className="origin-list">
                {item.original_messages.slice(0, 4).map((message, index) => (
                  <article className="origin-row" key={message.id}>
                    <div className="origin-main">
                      <strong title={message.channel_name || message.channel_username || ""}>
                        {message.channel_name || message.channel_username || "未知频道"}
                      </strong>
                      <small>
                        {message.message_id ? `#${message.message_id}` : "入库消息"}
                        {message.published_at ? ` · ${formatDate(message.published_at)}` : ""}
                      </small>
                      {message.raw_text ? (
                        <small className="origin-preview" title={messagePreview(message.raw_text)}>
                          {messagePreview(message.raw_text)}
                        </small>
                      ) : null}
                    </div>
                    {message.message_url ? (
                      <a className="origin-link" href={message.message_url} target="_blank" rel="noopener noreferrer">
                        打开消息 <ExternalLink size={11} aria-hidden="true" />
                      </a>
                    ) : null}
                    {message.raw_text ? (
                      <details className="origin-content" open={index === 0}>
                        <summary>{index === 0 ? "消息原文" : "查看完整消息"}</summary>
                        <pre>{message.raw_text}</pre>
                      </details>
                    ) : null}
                  </article>
                ))}
              </div>
              {item.original_messages.length > 4 ? (
                <p className="origin-more">另有 {item.original_messages.length - 4} 条来源消息</p>
              ) : null}
            </section>
          ) : null}

          {!readOnly && <ImdbEvidence lookup={item.imdb_lookup} />}
        </div>
      ) : null}
    </aside>
  );
}
