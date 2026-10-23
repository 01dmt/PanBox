import { ChevronLeft, ChevronRight, Film } from "lucide-react";
import { formatNumber, posterUrl, qualityList } from "../lib/format";

const regionNames = new Intl.DisplayNames(["zh-CN"], { type: "region", style: "short" });

function parseMeta(item, isTv) {
  const parts = [];
  const year = item.media_year || item.year || item.release_date?.slice(0, 4);
  if (year) parts.push(year);

  // Country
  let countryName = null;
  try {
    const countries = JSON.parse(item.origin_country_json || "[]");
    if (countries[0]) {
      const code = countries[0];
      try { countryName = regionNames.of(code) || code; } catch { countryName = code; }
    }
  } catch {}

  // Genre
  let genreName = null;
  try {
    const genres = JSON.parse(item.genres_json || "[]");
    if (genres[0]?.name) genreName = genres[0].name;
  } catch {}

  if (isTv) {
    // RE0 剧集格式: 年份 · 地区 · 集数
    if (countryName) parts.push(countryName);
    if (item.episode_count) {
      parts.push(`${item.episode_count} 集`);
    } else if (item.max_season) {
      parts.push(`${item.max_season} 季`);
    } else if (item.source_count) {
      parts.push(`${formatNumber(item.source_count)} 个来源`);
    }
  } else {
    // RE0 电影格式: 年份 · 题材 · 地区
    if (genreName) parts.push(genreName);
    if (countryName) parts.push(countryName);
    if (parts.length <= 1 && item.source_count) {
      parts.push(`${formatNumber(item.source_count)} 个来源`);
    }
  }

  return parts.join(" · ") || "年份未知";
}

export default function Re0PosterGrid({
  items = [],
  loading,
  selectedId,
  mode = "movie",
  onSelect,
  page = 1,
  pages = 1,
  total = 0,
  pageSize = 36,
  onPageChange,
  onPageSizeChange,
  onClearFilters,
}) {
  return (
    <section className="re0-grid-section" aria-label="影视网格">
      {loading ? (
        <div className="re0-poster-grid">
          {Array.from({ length: 18 }, (_, index) => (
            <div className="re0-skeleton-card" key={index} />
          ))}
        </div>
      ) : !items.length ? (
        <div className="re0-empty-state">
          <Film size={36} />
          <strong>没有找到符合条件的{mode === "tv" ? "剧集" : "电影"}</strong>
          <p>尝试切换类型、地区或清除当前筛选条件</p>
          <button type="button" onClick={onClearFilters}>
            清除全部筛选
          </button>
        </div>
      ) : (
        <div className="re0-poster-grid">
          {items.map((item) => {
            const title = item.tmdb_title || item.title || "未命名作品";
            const quality = qualityList(item.matched_qualities || item.qualities)[0];
            const rating = Number(item.vote_average);
            const isSelected = selectedId === item.id;
            const isTv = mode === "tv" || item.media_type === "tv";
            const meta = parseMeta(item, isTv);
            const seriesStatus = (item.year && item.year >= 2025) ? "连载中" : "已完结";

            return (
              <article
                key={item.id}
                className={`re0-card ${isSelected ? "selected" : ""}`}
                onClick={() => onSelect(item.id)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => e.key === "Enter" && onSelect(item.id)}
                aria-pressed={isSelected}
              >
                <div className="re0-card-surface">
                  <div className="re0-card-poster">
                    <img
                      src={posterUrl(item.poster_path, "w342")}
                      alt={title}
                      loading="lazy"
                      onError={(e) => {
                        e.currentTarget.src = "/fallback-poster.png";
                      }}
                    />
                    <span className="re0-card-gradient" />

                    {/* Top badges: TV shows 【已完结/连载中】 on left, Movie is clean; Right is Quality */}
                    <div className="re0-card-top-badges">
                      {isTv ? (
                        <span className={`re0-badge-series-status ${seriesStatus === "连载中" ? "is-airing" : "is-ended"}`}>
                          {seriesStatus}
                        </span>
                      ) : (
                        <span className="re0-badge-spacer" />
                      )}
                      {quality && (
                        <span className={`re0-badge-quality ${quality === "2160P" ? "is-4k" : ""}`}>
                          {quality === "2160P" ? "4K" : quality}
                        </span>
                      )}
                    </div>

                    {/* Bottom rating badge */}
                    {rating > 0 && (
                      <div className="re0-card-rating">
                        <b>★ {rating.toFixed(1)}</b>
                        <span className="re0-card-rating-source">TMDB</span>
                      </div>
                    )}

                    {/* Hover overview overlay */}
                    {item.overview && (
                      <div className="re0-card-overview">
                        <p>{item.overview}</p>
                        <span className="re0-card-overview-hint">点击查看网盘资源 →</span>
                      </div>
                    )}
                  </div>

                  <div className="re0-card-copy">
                    <h3 className="re0-card-title" title={title}>
                      {title}
                    </h3>
                    <p className="re0-card-meta" title={meta}>
                      {meta}
                    </p>
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      )}

      {total > 0 && (
        <footer className="re0-pagination">
          <div className="re0-pagination-size">
            <span>每页</span>
            <select
              value={pageSize}
              onChange={(e) => onPageSizeChange(Number(e.target.value))}
              aria-label="每页数量"
            >
              <option value={24}>24</option>
              <option value={36}>36</option>
              <option value={48}>48</option>
              <option value={60}>60</option>
            </select>
          </div>

          <div className="re0-pagination-nav">
            <button
              type="button"
              className="re0-page-btn"
              disabled={page <= 1}
              onClick={() => onPageChange(page - 1)}
              aria-label="上一页"
            >
              <ChevronLeft size={16} />
            </button>
            <span className="re0-page-indicator">
              <b>{page}</b> / {pages}
            </span>
            <button
              type="button"
              className="re0-page-btn"
              disabled={page >= pages}
              onClick={() => onPageChange(page + 1)}
              aria-label="下一页"
            >
              <ChevronRight size={16} />
            </button>
          </div>

          <div className="re0-pagination-total">
            共 {formatNumber(total)} 部{mode === "tv" ? "剧集" : "电影"}
          </div>
        </footer>
      )}
    </section>
  );
}
