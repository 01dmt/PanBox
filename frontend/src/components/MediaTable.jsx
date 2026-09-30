import { useLayoutEffect, useRef } from "react";
import {
  AlertCircle,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  CircleHelp,
  Film,
} from "lucide-react";
import {
  displayMediaType,
  displayStatus,
  formatDate,
  formatNumber,
  posterUrl,
  qualityList,
} from "../lib/format";


function Status({ value, confidence }) {
  const Icon = value === "matched" ? CheckCircle2 : value === "error" || value === "not_found" ? AlertCircle : CircleHelp;
  return (
    <span className={`status status-${value || "pending"}`}>
      <Icon size={15} />
      <span>{displayStatus(value)}</span>
      {confidence ? <small>{Math.round(confidence * 100)}%</small> : null}
    </span>
  );
}


function LoadingRows() {
  return Array.from({ length: 8 }, (_, index) => (
    <div className="media-row skeleton-row" key={index}>
      <span className="skeleton poster-skeleton" />
      <span className="skeleton line wide" />
      <span className="skeleton line" />
      <span className="skeleton line" />
      <span className="skeleton line" />
    </div>
  ));
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
      <div className="media-table-head" role="row">
        <span>海报</span>
        <span>标题 / 年份 / 类型</span>
        <span>来源</span>
        <span>质量</span>
        <span>TMDB 状态</span>
        <span>更新时间</span>
      </div>
      <div className="media-table-body" ref={bodyRef} role="region" aria-label="资源列表" aria-busy={loading} tabIndex={0}>
        {loading ? <LoadingRows /> : null}
        {!loading && !items.length ? (
          <div className="empty-state">
            <Film size={28} />
            <strong>没有符合条件的记录</strong>
            <button type="button" className="button small" onClick={onClearFilters}>清除筛选</button>
          </div>
        ) : null}
        {!loading
          ? items.map((item) => {
              const qualities = qualityList("matched_qualities" in item ? item.matched_qualities : item.qualities);
              const matchingSources = item.matched_source_count ?? item.source_count;
              return (
                <button
                  type="button"
                  className={`media-row ${selectedId === item.id ? "selected" : ""}`}
                  key={item.id}
                  onClick={() => onSelect(item.id)}
                >
                  <img
                    className="poster-thumb"
                    src={posterUrl(item.poster_path, "w185")}
                    alt=""
                    loading="lazy"
                  />
                  <span className="title-cell">
                    <strong>{item.tmdb_title || item.title}</strong>
                    <small>
                      {item.media_year || item.year || item.release_date?.slice(0, 4) || "年份未知"}
                      <i>·</i>
                      {displayMediaType(item.media_type)}
                    </small>
                    {item.original_title && item.original_title !== item.tmdb_title ? (
                      <em>{item.original_title}</em>
                    ) : null}
                  </span>
                  <span className="source-cell">
                    <strong>{item.source_count ? `${formatNumber(matchingSources)} 条来源` : "无资源"}</strong>
                    <small>{matchingSources < item.source_count ? `符合筛选 / 共 ${formatNumber(item.source_count)} 条` : `115: ${formatNumber(item.source_115_count)} · ED2K: ${formatNumber(item.source_ed2k_count)}`}</small>
                    {item.episode_count ? <em>{item.episode_count} 集 / {item.max_season || 1} 季</em> : null}
                  </span>
                  <span className="quality-cell">
                    {qualities.length ? qualities.map((quality) => <small key={quality}>{quality}</small>) : <em>未识别</em>}
                  </span>
                  <span className="status-cell">
                    <Status value={item.tmdb_status} confidence={item.match_confidence} />
                    {item.tmdb_id ? <small>TMDB #{item.tmdb_id}</small> : null}
                  </span>
                  <span className="date-cell">{formatDate(item.updated_at)}</span>
                </button>
              );
            })
          : null}
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
