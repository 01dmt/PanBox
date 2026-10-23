import { FolderOpen, Loader2, Search } from "lucide-react";
import { formatNumber, posterUrl } from "../lib/format";


function SeriesCard({ item, active, onSelect }) {
  const poster = item.poster_path ? posterUrl(item.poster_path, "w342") : null;
  const years = item.year_from && item.year_to && item.year_from !== item.year_to
    ? `${item.year_from}–${item.year_to}`
    : item.year_from || item.year_to || "";
  return (
    <button
      type="button"
      className={`series-card ${active ? "is-active" : ""}`}
      onClick={() => onSelect(item.id)}
      aria-pressed={active}
    >
      <div className="series-card-poster">
        {poster ? <img src={poster} alt="" loading="lazy" /> : <FolderOpen size={30} strokeWidth={1.4} />}
        <span className="series-card-count">{formatNumber(item.media_count)} 部</span>
      </div>
      <div className="series-card-body">
        <strong title={item.title}>{item.title}</strong>
        {years ? <span>{years}</span> : null}
        <span>{`已匹配 ${formatNumber(item.matched_count)}/${formatNumber(item.media_count)}`}</span>
      </div>
    </button>
  );
}


export default function SeriesBrowser({ items = [], loading, detail, detailLoading, selectedId, onSelect, query = "", onQuery, onOpenMedia }) {
  return (
    <div className="series-browser">
      <section className="series-list">
        <header className="series-list-head">
          <div>
            <h2>系列</h2>
            <p>频道给系列里的每一部单独发消息，这里按系列名聚在一起；点开可看包含哪几部。</p>
          </div>
          <label className="series-search">
            <Search size={15} strokeWidth={1.8} />
            <input
              value={query}
              onChange={(event) => onQuery?.(event.target.value)}
              placeholder="搜索系列"
              aria-label="搜索系列"
            />
          </label>
        </header>

        {loading ? (
          <div className="series-empty"><Loader2 className="spin" size={18} /> 正在加载…</div>
        ) : items.length ? (
          <div className="series-grid">
            {items.map((item) => (
              <SeriesCard key={item.id} item={item} active={item.id === selectedId} onSelect={onSelect} />
            ))}
          </div>
        ) : (
          <div className="series-empty">还没有识别到系列。</div>
        )}
      </section>

      <aside className="series-detail">
        {detailLoading && !detail ? (
          <div className="series-empty"><Loader2 className="spin" size={18} /> 正在加载…</div>
        ) : detail ? (
          <>
            <header className="series-detail-head">
              <div>
                <h3>{detail.title}</h3>
                <span>{formatNumber(detail.items.length)} 部作品</span>
              </div>
            </header>
            <ol className="series-members">
              {detail.items.map((member) => {
                const poster = member.poster_path ? posterUrl(member.poster_path, "w185") : null;
                return (
                  <li key={member.id}>
                    <button type="button" className="series-member" onClick={() => onOpenMedia?.(member.id)}>
                      <span className="series-member-poster">
                        {poster ? <img src={poster} alt="" loading="lazy" /> : <FolderOpen size={16} strokeWidth={1.4} />}
                      </span>
                      <span className="series-member-text">
                        {/* 未匹配时标题只是系列名，真正能认出是哪一部的是发布文件名。 */}
                        <strong>{member.tmdb_title || member.title}</strong>
                        <span>
                          {member.is_bundle
                            // 整包分享拆不出成员：说明白，别让人以为这一格就是某一部。
                            ? `整包分享 · ${member.source_count} 个来源`
                            : [member.year, member.tmdb_status === "matched" ? "已匹配" : member.tmdb_status === "review" ? "待确认" : "待匹配", `${member.source_count} 个来源`]
                              .filter(Boolean)
                              .join(" · ")}
                        </span>
                        {!member.tmdb_title && member.sample_label ? (
                          <span className="series-member-raw" title={member.sample_label}>{member.sample_label}</span>
                        ) : null}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ol>
          </>
        ) : (
          <div className="series-empty">选择左侧一个系列查看包含的作品。</div>
        )}
      </aside>
    </div>
  );
}
