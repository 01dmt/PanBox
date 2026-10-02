import { useLayoutEffect, useRef } from "react";
import {
  ChevronLeft,
  ChevronRight,
  Film,
} from "lucide-react";
import {
  displayMediaType,
  displayResourceKind,
  displayStatus,
  formatNumber,
  posterUrl,
  qualityList,
} from "../lib/format";


function LoadingRows() {
  return <div className="poster-grid poster-grid-loading">{Array.from({ length: 12 }, (_, index) => <div className="poster-card-skeleton" key={index}><span className="skeleton" /><i className="skeleton" /><i className="skeleton short" /></div>)}</div>;
}

function MediaCard({ item, selectedId, onSelect }) {
  const qualities = qualityList("matched_qualities" in item ? item.matched_qualities : item.qualities);
  const matchingSources = item.matched_source_count ?? item.source_count;
  const title = item.tmdb_title || item.title;
  const year = item.media_year || item.year || item.release_date?.slice(0, 4) || "年份未知";
  const episodeInfo = item.media_type === "tv" && (item.episode_count || item.max_season)
    ? `${item.max_season ? `${item.max_season} 季` : ""}${item.max_season && item.episode_count ? " · " : ""}${item.episode_count ? `${item.episode_count} 集` : ""}` : null;
  return <button type="button" className={`media-card ${selectedId === item.id ? "selected" : ""}`} aria-pressed={selectedId === item.id} onClick={() => onSelect(item.id)}>
    <div className="media-card-poster"><img src={posterUrl(item.poster_path, "w342")} alt={title} loading="lazy" /><span className="media-card-type">{displayResourceKind(item.resource_kind)}</span>{qualities[0] ? <span className="media-card-quality">{qualities[0]}</span> : null}<span className="media-card-rating">★ {item.vote_average ? Number(item.vote_average).toFixed(1) : "—"}</span></div>
    <div className="media-card-meta"><strong title={title}>{title}</strong><span>{year} · {matchingSources ? `${formatNumber(matchingSources)} 个来源` : "无资源"}{episodeInfo ? ` · ${episodeInfo}` : ""}</span><small className={`card-status card-status-${item.tmdb_status || "pending"}`}>{displayStatus(item.tmdb_status)}</small></div>
  </button>;
}


export default function MediaTable({
  items,
  loading,
  selectedId,
  onSelect,
  page,
  pages,
  total,
  pageSize,
  onPageChange,
  onPageSizeChange,
  onClearFilters,
  scrollKey,
}) {
  const bodyRef = useRef(null);
  const tableRef = useRef(null);

  useLayoutEffect(() => {
    bodyRef.current?.scrollTo({ top: 0, left: 0 });
    // On phones the document scrolls; keep the first row below the sticky search bar.
    if (window.matchMedia("(max-width: 760px)").matches && window.scrollY > 0) {
      const panel = tableRef.current?.closest(".library-panel");
      const headerBottom = Math.max(
        document.querySelector(".topbar")?.getBoundingClientRect().bottom ?? 0,
        document.querySelector(".global-search")?.getBoundingClientRect().bottom ?? 0,
      );
      if (panel) window.scrollTo({ top: Math.max(0, panel.getBoundingClientRect().top + window.scrollY - headerBottom - 8) });
    }
  }, [page, pageSize, scrollKey]);

  return (
    <div className="media-table-wrap" ref={tableRef}>
      <div className="poster-wall-toolbar"><strong>共 {formatNumber(total)} 部</strong><span>点击海报查看资源详情</span></div>
      <div className="media-table-body" ref={bodyRef} role="region" aria-label="资源列表" aria-busy={loading} tabIndex={0}>
        {loading ? <LoadingRows /> : null}
        {!loading && !items.length ? (
          <div className="empty-state">
            <Film size={28} />
            <strong>没有符合条件的记录</strong>
            <button type="button" className="button small" onClick={onClearFilters}>清除筛选</button>
          </div>
        ) : null}
        {!loading ? <div className="poster-grid">{items.map((item) => <MediaCard item={item} selectedId={selectedId} onSelect={onSelect} key={item.id} />)}</div> : null}
      </div>

      <footer className="table-footer">
        <label>
          每页
          <select aria-label="每页数量" value={pageSize} onChange={(event) => onPageSizeChange(Number(event.target.value))}>
            <option value={20}>20</option>
            <option value={30}>30</option>
            <option value={50}>50</option>
            <option value={100}>100</option>
          </select>
        </label>
        <div className="pagination">
          <button type="button" className="icon-only" disabled={page <= 1} onClick={() => onPageChange(page - 1)} aria-label="上一页">
            <ChevronLeft size={17} />
          </button>
          <span>第 {page} / {pages} 页</span>
          <button type="button" className="icon-only" disabled={page >= pages} onClick={() => onPageChange(page + 1)} aria-label="下一页">
            <ChevronRight size={17} />
          </button>
        </div>
        <span>共 {formatNumber(total)} 条</span>
      </footer>
    </div>
  );
}
