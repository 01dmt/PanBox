import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  Film,
  LayoutGrid,
  LayoutList,
  LoaderCircle,
  RefreshCw,
  Search,
  SlidersHorizontal,
  Sparkles,
  X,
} from "lucide-react";
import Inspector from "./Inspector";
import { displayMediaType, formatDateTime, formatNumber, posterUrl } from "../lib/format";

const STATUS_TABS = [
  { id: "all", label: "全部" },
  { id: "review", label: "待确认" },
  { id: "not_found", label: "未找到" },
  { id: "unsearched", label: "未搜索" },
];

const TYPE_OPTIONS = [["all", "全部类型"], ["movie", "电影"], ["tv", "剧集"], ["unknown", "待判断"]];
const SOURCE_OPTIONS = [["all", "全部来源"], ["115", "115 分享"], ["123", "123 网盘"], ["guangya", "光鸭云盘"], ["ed2k", "ED2K"]];
const SORT_OPTIONS = [
  ["updated_desc", "最近更新"],
  ["confidence_asc", "低置信度优先"],
  ["title_asc", "标题排序"],
  ["year_desc", "年份从新到旧"],
  ["sources_desc", "来源最多"],
];
const QUALITY_OPTIONS = [["all", "全部清晰度"], ["2160P", "4K / 2160P"], ["1080P", "1080P"], ["1080I", "1080I"], ["720P", "720P"], ["480P", "480P"], ["unknown", "未识别"]];
const CODEC_OPTIONS = [["all", "全部编码"], ["h265", "H.265 / HEVC"], ["h264", "H.264 / AVC"], ["av1", "AV1"], ["unknown", "未识别"]];
const HDR_OPTIONS = [["all", "全部动态范围"], ["dv", "杜比视界 / DV"], ["hdr", "HDR / HDR10+"], ["sdr", "SDR"], ["unknown", "未识别"]];
const regionNames = new Intl.DisplayNames(["zh-CN"], { type: "region", style: "short" });

function queueStatusLabel(status) {
  if (status === "review") return "待确认";
  if (status === "not_found") return "未找到";
  if (status === "error") return "匹配错误";
  return "未搜索";
}

function queueStatusTone(status) {
  if (status === "review") return "review";
  if (status === "not_found" || status === "error") return "bad";
  return "idle";
}

function mediaTitle(item) {
  return item.tmdb_title || item.title || "未命名作品";
}

function mediaYear(item) {
  return item.media_year || item.year || item.release_date?.slice(0, 4) || null;
}

function sourceCount(item) {
  return item.matched_source_count ?? item.source_count ?? 0;
}

function scoreTone(score) {
  if (score >= 0.85) return "high";
  if (score >= 0.65) return "mid";
  return "low";
}

function Cover({ item, className = "" }) {
  // 未命中 TMDB 的作品没有海报，用轻量占位块代替同一张兜底图，避免整屏重复。
  if (!item.poster_path) {
    return (
      <span className={`pending-cover pending-cover-empty ${className}`} aria-hidden="true">
        <Film size={16} />
        <small>暂无海报</small>
      </span>
    );
  }
  return (
    <span className={`pending-cover ${className}`}>
      <img src={posterUrl(item.poster_path, "w92")} alt="" loading="lazy" />
    </span>
  );
}

function CandidateCell({ item }) {
  // 只要还留有候选就优先展示最高分，避免历史状态与候选数据不一致时把线索藏起来。
  if (item.top_candidate && item.tmdb_status !== "matched") {
    const top = item.top_candidate;
    const year = top.release_date?.slice(0, 4);
    return (
      <span className="pending-candidate">
        <span className={`pending-score tone-${scoreTone(Number(top.score) || 0)}`}>{Math.round((Number(top.score) || 0) * 100)}%</span>
        <span className="pending-candidate-title" title={top.title}>{top.title}{year ? ` (${year})` : ""}</span>
        <small>{formatNumber(item.candidate_count)} 个候选</small>
      </span>
    );
  }
  if (item.tmdb_status === "review") {
    return <span className="pending-empty-signal">候选待重新生成</span>;
  }
  if (item.tmdb_status === "not_found") {
    return <span className="pending-empty-signal" title="TMDB 与 IMDb 均未命中">无候选</span>;
  }
  if (item.tmdb_status === "error") {
    return <span className="pending-empty-signal bad" title={item.last_tmdb_error || ""}>匹配失败，等待重试</span>;
  }
  return <span className="pending-empty-signal">尚未搜索候选</span>;
}

function QueueRow({ item, selected, onSelect }) {
  const status = item.tmdb_status || "pending";
  return (
    <button
      type="button"
      className={`pending-row ${selected ? "selected" : ""}`}
      onClick={() => onSelect(item.id)}
      aria-pressed={selected}
    >
      <Cover item={item} />
      <span className="pending-title-cell">
        <strong title={mediaTitle(item)}>{mediaTitle(item)}</strong>
        <small>
          {mediaYear(item) || "年份未知"} · {displayMediaType(item.media_type)} · {formatNumber(sourceCount(item))} 个来源
        </small>
      </span>
      <span className={`pending-state tone-${queueStatusTone(status)}`}><i aria-hidden="true" />{queueStatusLabel(status)}</span>
      <CandidateCell item={item} />
      <span className="pending-time">{formatDateTime(item.updated_at)}</span>
      <ChevronRight className="pending-row-arrow" size={15} aria-hidden="true" />
    </button>
  );
}

function QueueCard({ item, selected, onSelect }) {
  const status = item.tmdb_status || "pending";
  return (
    <button
      type="button"
      className={`pending-card ${selected ? "selected" : ""}`}
      onClick={() => onSelect(item.id)}
      aria-pressed={selected}
    >
      <span className="pending-card-poster">
        <Cover item={item} />
        <span className={`pending-state tone-${queueStatusTone(status)}`}><i aria-hidden="true" />{queueStatusLabel(status)}</span>
      </span>
      <strong title={mediaTitle(item)}>{mediaTitle(item)}</strong>
      <span className="pending-card-meta">
        {mediaYear(item) || "年份未知"} · {displayMediaType(item.media_type)} · {formatNumber(sourceCount(item))} 来源
        {item.tmdb_status === "review" && item.candidate_count ? ` · ${formatNumber(item.candidate_count)} 候选` : ""}
      </span>
      <CandidateCell item={item} />
    </button>
  );
}

function SkeletonRows() {
  return (
    <div className="pending-skeleton" aria-hidden="true">
      {Array.from({ length: 8 }, (_, index) => (
        <div key={index}><span className="skeleton" /><i className="skeleton" /><i className="skeleton short" /></div>
      ))}
    </div>
  );
}

export default function PendingView({
  stats,
  filters,
  options,
  search,
  searchRef,
  pageData,
  loading,
  selectedId,
  selectedItem,
  detailLoading,
  tmdbConfigured,
  mobileInspectorOpen,
  syncing,
  onSearch,
  onChangeFilters,
  onClearSearch,
  onRefresh,
  onSelect,
  onCloseInspector,
  onSync,
  onSearchCandidates,
  onLink,
  onReject,
  onUnlink,
  onUpdate,
}) {
  const metadataCount = Number(stats?.media?.metadata || 0);
  const [expanded, setExpanded] = useState(false);
  const [layout, setLayout] = useState(() => {
    try {
      return localStorage.getItem("panbox-pending-layout") === "grid" ? "grid" : "list";
    } catch {
      return "list";
    }
  });
  const [pageDraft, setPageDraft] = useState(String(pageData.page || 1));
  const listRef = useRef(null);

  const counts = {
    all: stats?.media?.pending,
    review: stats?.media?.review,
    not_found: stats?.media?.not_found,
    unsearched: stats?.media?.unsearched,
  };

  const years = options?.years || [];
  const decades = useMemo(() => [...new Set(years.map((year) => Math.floor(year / 10) * 10))], [years]);
  const genreOptions = useMemo(
    () => [["all", "全部题材"], ...(options?.genres || []).map(({ id, name }) => [String(id), ({ 10765: "科幻与奇幻", 10768: "战争与政治" })[id] || name])],
    [options],
  );
  const countryOptions = useMemo(
    () => [["all", "全部国家 / 地区"], ...(options?.countries || []).map((code) => [code, regionNames.of(code)])],
    [options],
  );

  const activeAdvanced = ["year", "genre", "country", "quality", "codec", "hdr"].filter((key) => filters[key] && filters[key] !== "all").length;
  const filterActive = Boolean(search.trim()) || ["status", "type", "source", "year", "genre", "country", "quality", "codec", "hdr"]
    .some((key) => filters[key] && filters[key] !== "all");

  const page = pageData.page || 1;
  const pages = pageData.pages || 1;

  useEffect(() => {
    setPageDraft(String(page));
  }, [page]);

  useLayoutEffect(() => {
    listRef.current?.scrollTo({ top: 0, left: 0 });
  }, [page, layout, filters]);

  const changeLayout = (next) => {
    setLayout(next);
    try {
      localStorage.setItem("panbox-pending-layout", next);
    } catch {
      /* localStorage 不可用时仅保留当前会话的布局。 */
    }
  };

  const resetFilters = () => {
    onClearSearch();
    onChangeFilters({
      status: "all",
      type: "all",
      source: "all",
      availability: "all",
      year: "all",
      genre: "all",
      country: "all",
      quality: "all",
      codec: "all",
      hdr: "all",
      sort: "updated_desc",
      page: 1,
    });
  };

  const jumpToPage = () => {
    const next = Math.min(Math.max(Number.parseInt(pageDraft, 10) || 1, 1), pages);
    setPageDraft(String(next));
    if (next !== page) onChangeFilters({ page: next });
  };

  const selectOptions = (choices) => choices.map(([value, label]) => <option key={value} value={value}>{label}</option>);

  return (
    <main className="pending-page" aria-label="待匹配审核">
      <header className="dash-top pending-top">
        <div>
          <span className="dash-kicker">CURATION / 匹配审核</span>
          <h1>待匹配</h1>
          <p>按状态逐条复核候选，选中作品后在详情面板完成关联。</p>
        </div>
        <div className="pending-top-actions">
          {!tmdbConfigured ? <span className="pending-tmdb-warning">未配置 TMDB，候选搜索不可用</span> : null}
          <button type="button" className="dash-btn primary" onClick={onSync} disabled={syncing}>
            {syncing ? <LoaderCircle size={15} className="spin" /> : <Sparkles size={15} />}
            {syncing ? "匹配中…" : "自动匹配 20 条"}
          </button>
        </div>
      </header>

      <section className="pending-tabs" role="tablist" aria-label="按匹配状态筛选">
        {STATUS_TABS.map((tab) => {
          const selected = (filters.status || "all") === tab.id || (tab.id === "all" && filters.status === "pending");
          return (
            <button
              key={tab.id}
              type="button"
              role="tab"
              aria-selected={selected}
              className={selected ? "active" : ""}
              onClick={() => onChangeFilters({ status: tab.id, page: 1 })}
            >
              <span>{tab.label}</span>
              <strong>{formatNumber(counts[tab.id])}</strong>
            </button>
          );
        })}
      </section>

      <div className="pending-workbench">
        <section className="pending-queue" aria-label="待处理作品">
          <div className="pending-toolbar">
            <div className="pending-search" role="search">
              <Search size={15} aria-hidden="true" />
              <input
                ref={searchRef}
                type="search"
                value={search}
                onChange={(event) => onSearch(event.target.value)}
                placeholder="搜索片名、年份、TMDB ID、缓存文件名"
                aria-label="搜索待匹配作品"
              />
              {search ? <button type="button" onClick={() => onSearch("")} aria-label="清空搜索"><X size={13} /></button> : null}
            </div>
            <select className="pending-select" value={filters.type || "all"} onChange={(event) => onChangeFilters({ type: event.target.value })} aria-label="按类型筛选">
              {selectOptions(TYPE_OPTIONS)}
            </select>
            <select className="pending-select" value={filters.source || "all"} onChange={(event) => onChangeFilters({ source: event.target.value })} aria-label="按来源筛选">
              {selectOptions(SOURCE_OPTIONS)}
            </select>
            <select className="pending-select" value={filters.sort || "updated_desc"} onChange={(event) => onChangeFilters({ sort: event.target.value })} aria-label="排序方式">
              {selectOptions(SORT_OPTIONS)}
            </select>
            <button
              type="button"
              className={`pending-more ${expanded ? "active" : ""}`}
              aria-expanded={expanded}
              aria-controls="pending-advanced-filters"
              onClick={() => setExpanded((value) => !value)}
            >
              <SlidersHorizontal size={14} />
              筛选{activeAdvanced ? <b>{activeAdvanced}</b> : null}
            </button>
            {filterActive ? <button type="button" className="pending-reset" onClick={resetFilters}>重置</button> : null}
            <div className="pending-view-actions">
              <div className="pending-view-toggle" role="group" aria-label="列表或海报墙">
                <button type="button" className={layout === "list" ? "active" : ""} aria-pressed={layout === "list"} onClick={() => changeLayout("list")} title="列表视图" aria-label="列表视图">
                  <LayoutList size={15} />
                </button>
                <button type="button" className={layout === "grid" ? "active" : ""} aria-pressed={layout === "grid"} onClick={() => changeLayout("grid")} title="海报墙视图" aria-label="海报墙视图">
                  <LayoutGrid size={15} />
                </button>
              </div>
              <button type="button" className="dash-icon-btn" onClick={onRefresh} disabled={loading} title="刷新列表" aria-label="刷新列表">
                <RefreshCw size={15} className={loading ? "spin" : ""} />
              </button>
            </div>
          </div>

          {expanded ? (
            <div className="pending-advanced" id="pending-advanced-filters">
              <label>年份
                <select value={filters.year || "all"} onChange={(event) => onChangeFilters({ year: event.target.value })}>
                  <option value="all">全部年份</option>
                  <option value="unknown">年份未知</option>
                  <optgroup label="年代">{decades.map((year) => <option key={year} value={`${year}s`}>{year} - {year + 9}</option>)}</optgroup>
                  <optgroup label="具体年份">{years.map((year) => <option key={year} value={String(year)}>{year} 年</option>)}</optgroup>
                </select>
              </label>
              <label>题材<select value={filters.genre || "all"} onChange={(event) => onChangeFilters({ genre: event.target.value })}>{selectOptions(genreOptions)}</select></label>
              <label>国家 / 地区<select value={filters.country || "all"} onChange={(event) => onChangeFilters({ country: event.target.value })}>{selectOptions(countryOptions)}</select></label>
              <label>清晰度<select value={filters.quality || "all"} onChange={(event) => onChangeFilters({ quality: event.target.value })}>{selectOptions(QUALITY_OPTIONS)}</select></label>
              <label>编码<select value={filters.codec || "all"} onChange={(event) => onChangeFilters({ codec: event.target.value })}>{selectOptions(CODEC_OPTIONS)}</select></label>
              <label>动态范围<select value={filters.hdr || "all"} onChange={(event) => onChangeFilters({ hdr: event.target.value })}>{selectOptions(HDR_OPTIONS)}</select></label>
            </div>
          ) : null}

          <div className="pending-list" ref={listRef} aria-busy={loading}>
            {loading && !pageData.items.length ? <SkeletonRows /> : null}
            {!loading && !pageData.items.length ? (
              <div className="pending-empty">
                <Film size={26} aria-hidden="true" />
                <strong>没有符合条件的待处理作品</strong>
                <span>调整状态分栏或筛选条件后再试。</span>
                <button type="button" className="dash-btn" onClick={resetFilters}>清除筛选</button>
              </div>
            ) : null}
            {layout === "list" && pageData.items.length ? (
              <>
                <div className="pending-list-head" aria-hidden="true">
                  <span />
                  <span>作品</span>
                  <span>状态</span>
                  <span>最高候选</span>
                  <span>更新</span>
                  <span />
                </div>
                <div className={`pending-rows ${loading ? "is-loading" : ""}`}>
                  {pageData.items.map((item) => (
                    <QueueRow key={item.id} item={item} selected={selectedId === item.id} onSelect={onSelect} />
                  ))}
                </div>
              </>
            ) : null}
            {layout === "grid" && pageData.items.length ? (
              <div className={`pending-cards ${loading ? "is-loading" : ""}`}>
                {pageData.items.map((item) => (
                  <QueueCard key={item.id} item={item} selected={selectedId === item.id} onSelect={onSelect} />
                ))}
              </div>
            ) : null}
          </div>

          <footer className="pending-foot">
            <label>
              每页
              <select value={filters.pageSize || 20} onChange={(event) => onChangeFilters({ pageSize: Number(event.target.value) })} aria-label="每页数量">
                <option value={20}>20</option>
                <option value={30}>30</option>
                <option value={50}>50</option>
                <option value={100}>100</option>
              </select>
            </label>
            <div className="pending-pagination">
              <button type="button" className="icon-only" disabled={page <= 1} onClick={() => onChangeFilters({ page: 1 })} aria-label="第一页" title="第一页"><ChevronsLeft size={16} /></button>
              <button type="button" className="icon-only" disabled={page <= 1} onClick={() => onChangeFilters({ page: page - 1 })} aria-label="上一页" title="上一页"><ChevronLeft size={16} /></button>
              <span className="pending-page-jump">
                第
                <input
                  type="number"
                  min="1"
                  max={pages}
                  value={pageDraft}
                  onChange={(event) => setPageDraft(event.target.value)}
                  onBlur={jumpToPage}
                  onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); jumpToPage(); } }}
                  aria-label="跳转页码"
                />
                / {pages} 页
              </span>
              <button type="button" className="icon-only" disabled={page >= pages} onClick={() => onChangeFilters({ page: page + 1 })} aria-label="下一页" title="下一页"><ChevronRight size={16} /></button>
              <button type="button" className="icon-only" disabled={page >= pages} onClick={() => onChangeFilters({ page: pages })} aria-label="最后一页" title="最后一页"><ChevronsRight size={16} /></button>
            </div>
            <span className="pending-foot-total">
              共 {formatNumber(pageData.total)} 条
              {metadataCount > 0 ? (
                <span className="pending-meta-note" title="仅有海报、NFO 等刮削附属文件，没有可播放的视频/音频；字幕和歌词不在此列">
                  {" · 已隐藏 "}{formatNumber(metadataCount)} 条元数据
                </span>
              ) : null}
            </span>
          </footer>
        </section>

        <Inspector
          item={selectedItem}
          sourceIds={pageData.items.find((item) => item.id === selectedId)?.matched_source_ids}
          loading={detailLoading}
          tmdbConfigured={tmdbConfigured}
          mobileOpen={mobileInspectorOpen}
          onClose={onCloseInspector}
          onSearch={onSearchCandidates}
          onLink={onLink}
          onReject={onReject}
          onUnlink={onUnlink}
          onUpdate={onUpdate}
        />
      </div>

      {mobileInspectorOpen ? <div className="pending-drawer-backdrop" onClick={onCloseInspector} aria-hidden="true" /> : null}
    </main>
  );
}
