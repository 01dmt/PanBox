import { RotateCcw, SlidersHorizontal } from "lucide-react";
import { formatNumber } from "../lib/format";

export default function CinematicFilters({ filters, options, total, onChange, onReset, onMore }) {
  const genres = (options?.genres || []).slice(0, 10);
  const years = (options?.years || []).slice(0, 8);
  return <section className="cinema-filters" aria-label="影视资料库筛选">
    <div className="cinema-section-title"><div><span>MOVIES & TV SHOWS</span><h2>影视资料库</h2></div><strong>共 {formatNumber(total)} 部</strong></div>
    <div className="cinema-switch"><button type="button" className={filters.type === "all" ? "active" : ""} onClick={() => onChange({ type: "all" })}>全部</button><button type="button" className={filters.type === "movie" ? "active" : ""} onClick={() => onChange({ type: "movie" })}>电影</button><button type="button" className={filters.type === "tv" ? "active" : ""} onClick={() => onChange({ type: "tv" })}>剧集</button></div>
    <div className="cinema-filter-line"><span>题材</span><button type="button" className={filters.genre === "all" ? "active" : ""} onClick={() => onChange({ genre: "all" })}>全部</button>{genres.map((genre) => <button type="button" className={filters.genre === String(genre.id) ? "active" : ""} onClick={() => onChange({ genre: String(genre.id) })} key={genre.id}>{genre.name}</button>)}</div>
    <div className="cinema-filter-line"><span>年份</span><button type="button" className={filters.year === "all" ? "active" : ""} onClick={() => onChange({ year: "all" })}>全部</button>{years.map((year) => <button type="button" className={filters.year === String(year) ? "active" : ""} onClick={() => onChange({ year: String(year) })} key={year}>{year}</button>)}<button type="button" className="cinema-more" onClick={onMore}><SlidersHorizontal size={13} />更多筛选</button><button type="button" className="cinema-reset" onClick={onReset}><RotateCcw size={13} />重置</button></div>
  </section>;
}
