import { displayMediaType, posterUrl } from "../lib/format";

function genres(value) {
  try {
    const parsed = JSON.parse(value || "[]");
    return Array.isArray(parsed) ? parsed.filter((entry) => typeof entry?.name === "string").slice(0, 3).map((entry) => entry.name) : [];
  } catch { return []; }
}

export default function LibraryHero({ item, onOpen }) {
  const title = item?.tmdb_title || item?.title;
  const backdrop = item?.backdrop_path ? posterUrl(item.backdrop_path, "w1280") : null;
  return (
    <section className="cinema-hero" style={backdrop ? { backgroundImage: `linear-gradient(90deg, rgba(7,8,14,.93) 0%, rgba(7,8,14,.64) 47%, rgba(7,8,14,.14) 100%), linear-gradient(0deg, #090a10, transparent 62%), url("${backdrop}")` } : undefined} aria-label="精选作品">
      {item ? <div className="cinema-hero-content">
        <span className="cinema-eyebrow">{[displayMediaType(item.media_type), ...genres(item.genres_json)].join(" · ")}</span>
        <h1>{title}</h1>
        <div className="cinema-hero-facts">
          <span>{item.media_year || item.year || item.release_date?.slice(0, 4) || "年份未知"}</span>
          {Number(item.vote_average) > 0 && <span>★ {Number(item.vote_average).toFixed(1)} TMDB</span>}
        </div>
        {item.overview && <p>{item.overview}</p>}
        <button type="button" onClick={() => onOpen(item.id)}>查看资源详情 →</button>
      </div> : <div className="cinema-hero-content"><span className="cinema-eyebrow">MOVIES & TV SHOWS</span><h1>影视资料库</h1><p>浏览已缓存的作品与网盘资源</p></div>}
    </section>
  );
}
