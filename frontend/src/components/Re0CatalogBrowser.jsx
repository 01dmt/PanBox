import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, RotateCcw, Search, X } from "lucide-react";
import { formatNumber } from "../lib/format";

// RE0 排序维度 (最新更新 / TMDB 评分 / 年份新到旧)
const SORT_OPTIONS = [
  ["updated_desc", "最新更新"],
  ["vote_desc", "TMDB 评分"],
  ["year_desc", "年份新到旧"],
];

// RE0 电影 18 大题材分类 (17 细分 + 全部)
const MOVIE_GENRES = [
  ["all", "全部"],
  ["28", "动作"],
  ["12", "冒险"],
  ["35", "喜剧"],
  ["80", "犯罪"],
  ["99", "纪录"],
  ["18", "剧情"],
  ["10751", "家庭"],
  ["14", "奇幻"],
  ["36", "历史"],
  ["27", "恐怖"],
  ["10402", "音乐"],
  ["9648", "悬疑"],
  ["10749", "爱情"],
  ["878", "科幻"],
  ["53", "惊悚"],
  ["10752", "战争"],
  ["37", "西部"],
];

// RE0 剧集 14 大专有题材分类 (13 细分 + 全部)
const TV_GENRES = [
  ["all", "全部"],
  ["10759", "动作冒险"],
  ["35", "喜剧"],
  ["80", "犯罪"],
  ["99", "纪录"],
  ["18", "剧情"],
  ["10751", "家庭"],
  ["10762", "儿童"],
  ["9648", "悬疑"],
  ["10765", "科幻奇幻"],
  ["10766", "肥皂剧"],
  ["10767", "脱口秀"],
  ["10768", "战争政治"],
  ["37", "西部"],
];

// RE0 7 大标准地区维度 (完全对齐 RE0: 全部 / 大陆 / 香港 / 台湾 / 日本 / 韩国 / 欧美)
const RE0_REGIONS = [
  ["all", "全部"],
  ["CN", "大陆"],
  ["HK", "香港"],
  ["TW", "台湾"],
  ["JP", "日本"],
  ["KR", "韩国"],
  ["EuropeAndAmerica", "欧美"],
];

// RE0 20 个连续年份序列 (当前年倒序 20 年: 2026 ~ 2007 + 更早)
const currentYear = new Date().getFullYear();
const RE0_YEARS = [
  ["all", "全部"],
  ...Array.from({ length: 20 }, (_, i) => {
    const y = String(currentYear - i);
    return [y, y];
  }),
  ["earlier", "更早"],
];

// RE0 剧集顶层大分类 Tabs
const TV_CATEGORIES = [
  ["all", "全部"],
  ["drama", "剧集"],
  ["anime", "动漫"],
  ["documentary", "纪录"],
  ["variety", "综艺"],
];

function ChipRow({ label, value, options = [], onSelect }) {
  const containerRef = useRef(null);
  const [canScrollLeft, setCanScrollLeft] = useState(false);
  const [canScrollRight, setCanScrollRight] = useState(false);

  const checkScroll = () => {
    const el = containerRef.current;
    if (!el) return;
    setCanScrollLeft(el.scrollLeft > 4);
    setCanScrollRight(el.scrollLeft < el.scrollWidth - el.clientWidth - 4);
  };

  useEffect(() => {
    checkScroll();
    const el = containerRef.current;
    if (!el) return;
    el.addEventListener("scroll", checkScroll, { passive: true });
    window.addEventListener("resize", checkScroll);
    return () => {
      el.removeEventListener("scroll", checkScroll);
      window.removeEventListener("resize", checkScroll);
    };
  }, [options]);

  const scroll = (direction) => {
    if (!containerRef.current) return;
    const delta = direction === "left" ? -280 : 280;
    containerRef.current.scrollBy({ left: delta, behavior: "smooth" });
  };

  return (
    <div className="re0-browser-row">
      <span className="re0-browser-row-label">{label}</span>
      <div className="re0-browser-scroll-wrap">
        {canScrollLeft && (
          <button
            type="button"
            className="re0-browser-scroll-btn left"
            onClick={() => scroll("left")}
            aria-label={`${label}向左滚动`}
            tabIndex={-1}
          >
            <ChevronLeft size={15} />
          </button>
        )}
        <div className="re0-browser-chips" ref={containerRef}>
          {options.map(([optVal, optLabel]) => {
            const isSelected = String(value) === String(optVal) || (!value && optVal === "all");
            return (
              <button
                key={optVal}
                type="button"
                className={`re0-browser-chip ${isSelected ? "active" : ""}`}
                onClick={() => onSelect(optVal)}
                aria-pressed={isSelected}
              >
                {optLabel}
              </button>
            );
          })}
        </div>
        {canScrollRight && (
          <button
            type="button"
            className="re0-browser-scroll-btn right"
            onClick={() => scroll("right")}
            aria-label={`${label}向右滚动`}
            tabIndex={-1}
          >
            <ChevronRight size={15} />
          </button>
        )}
      </div>
    </div>
  );
}

export default function Re0CatalogBrowser({
  filters,
  total,
  mode = "movie",
  search = "",
  onChange,
  onSearch,
  onReset,
}) {
  const genreList = mode === "tv" ? TV_GENRES : MOVIE_GENRES;
  const regions = RE0_REGIONS;
  const years = RE0_YEARS;

  // Compute active filters
  const activeChips = useMemo(() => {
    const chips = [];
    if (search.trim()) {
      chips.push({ key: "search", label: `搜索: ${search.trim()}`, onClear: () => onSearch?.("") });
    }
    if (mode === "tv" && filters.category && filters.category !== "all") {
      const match = TV_CATEGORIES.find(([v]) => v === filters.category);
      chips.push({ key: "category", label: `大类: ${match ? match[1] : filters.category}`, onClear: () => onChange({ category: "all" }) });
    }
    if (filters.genre && filters.genre !== "all") {
      const match = genreList.find(([v]) => v === String(filters.genre));
      chips.push({ key: "genre", label: `类型: ${match ? match[1] : filters.genre}`, onClear: () => onChange({ genre: "all" }) });
    }
    if (filters.country && filters.country !== "all") {
      const match = regions.find(([v]) => v === filters.country);
      chips.push({ key: "country", label: `地区: ${match ? match[1] : filters.country}`, onClear: () => onChange({ country: "all" }) });
    }
    if (filters.year && filters.year !== "all") {
      const match = years.find(([v]) => v === String(filters.year));
      chips.push({ key: "year", label: `年份: ${match ? match[1] : filters.year}`, onClear: () => onChange({ year: "all" }) });
    }
    if (filters.sort && filters.sort !== "updated_desc") {
      const match = SORT_OPTIONS.find(([v]) => v === filters.sort);
      chips.push({ key: "sort", label: `排序: ${match ? match[1] : filters.sort}`, onClear: () => onChange({ sort: "updated_desc" }) });
    }
    return chips;
  }, [filters, search, mode, genreList, regions, years, onChange, onSearch]);

  const canReset = activeChips.length > 0;

  return (
    <section className="re0-browser-panel" aria-label="影视分类筛选">
      {mode === "tv" && (
        <div className="re0-category-tabs" role="tablist" aria-label="剧集大分类">
          {TV_CATEGORIES.map(([catKey, catLabel]) => {
            const isSelected = (filters.category || "all") === catKey;
            return (
              <button
                key={catKey}
                type="button"
                role="tab"
                className={`re0-category-tab ${isSelected ? "active" : ""}`}
                aria-selected={isSelected}
                onClick={() => onChange({ category: catKey, genre: "all", page: 1 })}
              >
                {catLabel}
              </button>
            );
          })}
        </div>
      )}

      <div className="re0-browser-rows">
        <ChipRow
          label="类型"
          value={filters.genre || "all"}
          options={genreList}
          onSelect={(val) => onChange({ genre: val })}
        />
        <ChipRow
          label="地区"
          value={filters.country || "all"}
          options={regions}
          onSelect={(val) => onChange({ country: val })}
        />
        <ChipRow
          label="年份"
          value={filters.year || "all"}
          options={years}
          onSelect={(val) => onChange({ year: val })}
        />
        <ChipRow
          label="排序"
          value={filters.sort || "updated_desc"}
          options={SORT_OPTIONS}
          onSelect={(val) => onChange({ sort: val })}
        />
      </div>

      <div className="re0-browser-toolbar">
        <div className="re0-browser-summary">
          <strong className="re0-browser-count">
            {search.trim() ? `检索到 ${formatNumber(total)} 部${mode === "tv" ? "剧集" : "电影"}` : `共 ${formatNumber(total)} 部${mode === "tv" ? "剧集" : "电影"}`}
          </strong>
          {activeChips.length > 0 && (
            <div className="re0-active-chips" aria-label="已选筛选条件">
              {activeChips.map((chip) => (
                <span key={chip.key} className="re0-active-chip">
                  <span>{chip.label}</span>
                  <button
                    type="button"
                    onClick={chip.onClear}
                    aria-label={`移除${chip.label}`}
                  >
                    <X size={12} />
                  </button>
                </span>
              ))}
            </div>
          )}
        </div>

        <div className="re0-browser-tools">
          <form
            className="re0-search-box"
            role="search"
            onSubmit={(e) => e.preventDefault()}
          >
            <Search size={15} aria-hidden="true" />
            <input
              value={search}
              onChange={(e) => onSearch?.(e.target.value)}
              placeholder={`搜索${mode === "tv" ? "剧名" : "片名"}、年份、TMDB ID...`}
              aria-label="检索作品"
            />
            {search ? (
              <button
                type="button"
                onClick={() => onSearch?.("")}
                title="清空搜索"
                aria-label="清空搜索"
              >
                <X size={14} />
              </button>
            ) : null}
          </form>

          <button
            type="button"
            className="re0-reset-btn"
            onClick={onReset}
            disabled={!canReset}
            title="重置全部筛选"
            aria-label="重置全部筛选"
          >
            <RotateCcw size={15} />
            <span className="hide-sm">重置</span>
          </button>
        </div>
      </div>
    </section>
  );
}
