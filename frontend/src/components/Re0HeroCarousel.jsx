import { useEffect, useMemo, useState } from "react";
import { ChevronLeft, ChevronRight, Play } from "lucide-react";
import { displayMediaType, posterUrl, qualityList } from "../lib/format";

function parseGenres(value) {
  try {
    const parsed = JSON.parse(value || "[]");
    return Array.isArray(parsed)
      ? parsed.filter((entry) => typeof entry?.name === "string").slice(0, 3).map((entry) => entry.name)
      : [];
  } catch {
    return [];
  }
}

export default function Re0HeroCarousel({ items = [], mode = "movie", onOpen }) {
  // Select up to 6 featured items that have backdrop images matching the current mode
  const slides = useMemo(() => {
    const matching = items.filter((item) => (mode === "tv" ? item.media_type === "tv" : item.media_type !== "tv"));
    const pool = matching.length > 0 ? matching : items;
    const withBackdrop = pool.filter((item) => Boolean(item.backdrop_path));
    if (withBackdrop.length > 0) return withBackdrop.slice(0, 6);
    return pool.slice(0, 3);
  }, [items, mode]);

  const [currentIndex, setCurrentIndex] = useState(0);
  const [isPaused, setIsPaused] = useState(false);

  // Auto rotate slides every 6.5s
  useEffect(() => {
    if (slides.length <= 1 || isPaused) return;
    const timer = setInterval(() => {
      setCurrentIndex((prev) => (prev + 1) % slides.length);
    }, 6500);
    return () => clearInterval(timer);
  }, [slides.length, isPaused]);

  if (!slides.length) {
    return (
      <section className="re0-hero re0-hero--empty" aria-label="影视资料库">
        <div className="re0-hero-content">
          <span className="re0-hero-eyebrow">MOVIES & TV SHOWS</span>
          <h1 className="re0-hero-title">影视资料库</h1>
          <p className="re0-hero-overview">全库已收录影视资源与网盘资产</p>
        </div>
      </section>
    );
  }

  const currentItem = slides[currentIndex] || slides[0];
  const title = currentItem.tmdb_title || currentItem.title || "未命名影片";
  const backdrop = currentItem.backdrop_path ? posterUrl(currentItem.backdrop_path, "w1280") : null;
  const genres = parseGenres(currentItem.genres_json);
  const quality = qualityList(currentItem.matched_qualities || currentItem.qualities)[0];
  const rating = Number(currentItem.vote_average);
  const year = currentItem.media_year || currentItem.year || currentItem.release_date?.slice(0, 4);

  return (
    <section
      className="re0-hero"
      onMouseEnter={() => setIsPaused(true)}
      onMouseLeave={() => setIsPaused(false)}
      aria-label="精选推荐"
    >
      <div
        className="re0-hero-backdrop"
        style={backdrop ? { backgroundImage: `url("${backdrop}")` } : undefined}
      >
        <span className="re0-hero-shade re0-hero-shade--bottom" />
        <span className="re0-hero-shade re0-hero-shade--left" />
      </div>

      <div className="re0-hero-container">
        <div className="re0-hero-content">
          <p className="re0-hero-genres">
            {[displayMediaType(currentItem.media_type), ...genres].filter(Boolean).join(" · ")}
          </p>
          <h2 className="re0-hero-title" title={title}>{title}</h2>

          <div className="re0-hero-meta">
            {year && <span className="re0-hero-badge">{year}</span>}
            {rating > 0 && (
              <span className="re0-hero-badge re0-hero-badge--rating">
                <b>★ {rating.toFixed(1)}</b>
                <small>TMDB</small>
              </span>
            )}
            {quality && <span className="re0-hero-badge re0-hero-badge--quality">{quality}</span>}
            {currentItem.episode_count ? (
              <span className="re0-hero-badge">{currentItem.episode_count} 集</span>
            ) : null}
          </div>

          {currentItem.overview && (
            <p className="re0-hero-overview">{currentItem.overview}</p>
          )}

          <div className="re0-hero-actions">
            <button
              type="button"
              className="re0-hero-primary-btn"
              onClick={() => onOpen(currentItem.id)}
            >
              <Play size={16} fill="currentColor" />
              <span>查看详情</span>
            </button>
          </div>
        </div>

        {slides.length > 1 && (
          <div className="re0-hero-controls">
            <button
              type="button"
              className="re0-hero-nav-btn"
              onClick={() => setCurrentIndex((prev) => (prev - 1 + slides.length) % slides.length)}
              aria-label="上一部"
            >
              <ChevronLeft size={18} />
            </button>
            <div className="re0-hero-dots">
              {slides.map((slide, idx) => (
                <button
                  key={slide.id}
                  type="button"
                  className={`re0-hero-dot ${idx === currentIndex ? "active" : ""}`}
                  onClick={() => setCurrentIndex(idx)}
                  aria-label={`切换到第 ${idx + 1} 部推荐`}
                />
              ))}
            </div>
            <button
              type="button"
              className="re0-hero-nav-btn"
              onClick={() => setCurrentIndex((prev) => (prev + 1) % slides.length)}
              aria-label="下一部"
            >
              <ChevronRight size={18} />
            </button>
          </div>
        )}
      </div>
    </section>
  );
}
