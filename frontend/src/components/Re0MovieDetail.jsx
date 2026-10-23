import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ArrowLeft,
  Check,
  CheckCircle2,
  CircleAlert,
  Clipboard,
  Cloud,
  Copy,
  Disc,
  ExternalLink,
  Film,
  FolderKanban,
  HardDrive,
  Languages,
  Link,
  LoaderCircle,
  Monitor,
  RefreshCw,
  Search,
  Sparkles,
  Star,
  Subtitles,
  Unlink,
  X,
} from "lucide-react";
import { getMediaDetail, checkResource } from "../api";
import { displayMediaType, formatBytes, formatDate, formatNumber, posterUrl } from "../lib/format";

const regionNames = new Intl.DisplayNames(["zh-CN"], { type: "region", style: "short" });

const PROVIDER_LOGOS = {
  "115": "/providers/115.png",
  "123": "/providers/123.png",
  guangya: "/providers/guangya.png",
};

const PROVIDER_NAMES = {
  "115": "115 网盘",
  "123": "123 网盘",
  guangya: "光鸭云盘",
  ed2k: "ED2K",
  aliPan: "阿里云盘",
  quark: "夸克网盘",
};

function ProviderIcon({ providerId, size = 16 }) {
  const logo = PROVIDER_LOGOS[providerId];
  if (logo) {
    return <img src={logo} alt="" width={size} height={size} className="re0-provider-img" />;
  }
  if (providerId === "ed2k") {
    return <Link size={size} />;
  }
  return <Cloud size={size} />;
}

function parseJson(str, fallback = []) {
  try {
    return JSON.parse(str || "[]") || fallback;
  } catch {
    return fallback;
  }
}

function extractSourceFacts(source) {
  const meta = parseJson(source.metadata_json, {});
  const qualityTags = Array.isArray(meta.quality_tags) ? meta.quality_tags : [];
  const facts = [];

  // 1. Resolution / Quality
  const rawQuality = source.quality || qualityTags.find((t) => /2160p|4k|1080p|1080i|720p/i.test(t));
  if (rawQuality) {
    const qUpper = String(rawQuality).toUpperCase();
    facts.push({
      type: "quality",
      label: qUpper.replace("2160P", "4K / 2160P"),
      Icon: Monitor,
    });
  }

  // 2. Audio & Subtitles
  const audioSubParts = [];
  if (meta.subtitles) audioSubParts.push(meta.subtitles);
  if (meta.audio) audioSubParts.push(meta.audio);
  const audioSubTag = qualityTags.find((t) => /中字|双语|内封|简中|繁中|国配|国语|粤语|dtsx|atmos|ddp|flac/i.test(t));
  if (audioSubTag && !audioSubParts.includes(audioSubTag)) {
    audioSubParts.push(audioSubTag);
  }
  if (audioSubParts.length > 0) {
    facts.push({
      type: "audio_sub",
      label: audioSubParts.slice(0, 2).join(" · "),
      Icon: Subtitles,
    });
  }

  // 3. Source Version & Codec (片源/压制)
  const versionParts = [];
  const verTag = qualityTags.find((t) => /remux|bluray|web-dl|bdrip|uhd|hdr|dv|dolby/i.test(t));
  if (verTag) versionParts.push(verTag.toUpperCase());
  if (source.codec) versionParts.push(source.codec.toUpperCase());
  if (source.hdr && !versionParts.includes(source.hdr)) versionParts.push(source.hdr);
  if (versionParts.length > 0) {
    facts.push({
      type: "version",
      label: versionParts.slice(0, 2).join(" · "),
      Icon: Disc,
    });
  }

  // 4. File Size
  if (source.file_size) {
    facts.push({
      type: "size",
      label: formatBytes(source.file_size),
      Icon: HardDrive,
    });
  } else if (meta.file_size_formatted) {
    facts.push({
      type: "size",
      label: meta.file_size_formatted,
      Icon: HardDrive,
    });
  }

  return facts;
}

export default function Re0MovieDetail({
  mediaId,
  onClose,
  onLink,
  onUnlink,
  onSearchCandidates,
  tmdbConfigured,
}) {
  const [item, setItem] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [activeTab, setActiveTab] = useState("all");
  const [selectedQuality, setSelectedQuality] = useState("all");
  const [selectedStatus, setSelectedStatus] = useState("all");
  const [checkingId, setCheckingId] = useState(null);
  const [toast, setToast] = useState("");
  const [showCandidates, setShowCandidates] = useState(false);
  const [candidateSearching, setCandidateSearching] = useState(false);

  const loadDetail = useCallback(async () => {
    if (!mediaId) return;
    try {
      setLoading(true);
      setError("");
      const result = await getMediaDetail(mediaId);
      setItem(result);
    } catch (err) {
      setError(err.message || "获取影视详情失败");
    } finally {
      setLoading(false);
    }
  }, [mediaId]);

  useEffect(() => {
    loadDetail();
  }, [loadDetail]);

  const showToast = useCallback((msg) => {
    setToast(msg);
    setTimeout(() => setToast(""), 2400);
  }, []);

  const handleCopyLink = useCallback(async (url) => {
    if (!url) return;
    try {
      await navigator.clipboard.writeText(url);
      showToast("分享链接已复制");
    } catch {
      showToast("复制失败");
    }
  }, [showToast]);

  const handleCheck = useCallback(async (sourceId) => {
    try {
      setCheckingId(sourceId);
      await checkResource(sourceId);
      showToast("来源检查完成");
      await loadDetail();
    } catch (err) {
      showToast(err.message || "检查失败");
    } finally {
      setCheckingId(null);
    }
  }, [loadDetail, showToast]);

  const handleSearchCandidatesClick = useCallback(async () => {
    try {
      setCandidateSearching(true);
      setShowCandidates(true);
      await onSearchCandidates?.();
      await loadDetail();
    } catch (err) {
      showToast(err.message || "搜索候选失败");
    } finally {
      setCandidateSearching(false);
    }
  }, [loadDetail, onSearchCandidates, showToast]);

  // Aggregate providers present in this work's sources
  const providerTabs = useMemo(() => {
    if (!item?.sources?.length) return [];
    const counts = new Map();
    for (const s of item.sources) {
      const pid = s.source_type === "ed2k" ? "ed2k" : (s.provider || s.source_type || "other");
      counts.set(pid, (counts.get(pid) || 0) + 1);
    }
    const tabs = [{ id: "all", label: "全部网盘", count: item.sources.length }];
    for (const [pid, count] of counts.entries()) {
      tabs.push({
        id: pid,
        label: PROVIDER_NAMES[pid] || pid,
        count,
      });
    }
    return tabs;
  }, [item?.sources]);

  // Filter sources based on activeTab, selectedQuality, selectedStatus
  const filteredSources = useMemo(() => {
    if (!item?.sources?.length) return [];
    return item.sources.filter((s) => {
      const pid = s.source_type === "ed2k" ? "ed2k" : (s.provider || s.source_type || "other");
      if (activeTab !== "all" && pid !== activeTab) return false;
      if (selectedQuality !== "all") {
        const q = (s.quality || "").toUpperCase();
        if (selectedQuality === "2160P" && !q.includes("2160P") && !q.includes("4K")) return false;
        if (selectedQuality === "1080P" && !q.includes("1080")) return false;
        if (selectedQuality === "720P" && !q.includes("720")) return false;
      }
      if (selectedStatus === "available" && s.availability_status !== "available") return false;
      return true;
    });
  }, [item?.sources, activeTab, selectedQuality, selectedStatus]);

  const title = item?.tmdb_title || item?.title || "未命名作品";
  const originalTitle = item?.original_title && item?.original_title !== title ? item.original_title : null;
  const backdrop = item?.backdrop_path ? posterUrl(item.backdrop_path, "original") : null;
  const poster = posterUrl(item?.poster_path, "w500");
  const genres = parseJson(item?.genres_json);
  const countries = parseJson(item?.origin_country_json);
  const rating = Number(item?.vote_average || 0);
  const year = item?.media_year || item?.year || item?.release_date?.slice(0, 4);
  const confidence = item?.match_confidence ? Math.round(item.match_confidence * 100) : null;
  const isSeries = item?.resource_kind === "series" || (item?.media_type === "tv" && item?.episode_count);

  return (
    <div className="re0-movie-detail">
      {loading ? (
        <div className="re0-detail-loading" role="status" aria-live="polite">
          <LoaderCircle size={32} className="spin" />
          <span>正在载入影视资料...</span>
        </div>
      ) : error || !item ? (
        <div className="re0-detail-error">
          <CircleAlert size={36} />
          <h2>{error || "没有找到该作品"}</h2>
          <button type="button" onClick={onClose} className="re0-detail-back-btn">
            <ArrowLeft size={16} /> 返回影视库
          </button>
        </div>
      ) : (
        <>
          {/* Toast notice */}
          {toast && <div className="re0-detail-toast">{toast}</div>}

          {/* Top ambient blurred backdrop */}
          <div
            className="re0-detail-ambient"
            style={backdrop ? { backgroundImage: `url("${backdrop}")` } : undefined}
          >
            <div className="re0-detail-ambient-veil" />
          </div>

          <div className="re0-detail-wrapper">
        {/* Navigation Bar / Breadcrumb */}
        <div className="re0-detail-top-nav">
          <button type="button" onClick={onClose} className="re0-detail-back-btn">
            <ArrowLeft size={16} />
            <span>返回影视库</span>
          </button>
          <span className="re0-detail-breadcrumb">/ {title} ({year || "年份未知"})</span>
        </div>

        {/* Media Info Section */}
        <section className="re0-media-hero">
          <div className="re0-media-poster-box">
            <img src={poster} alt={title} className="re0-media-poster-img" />
          </div>

          <div className="re0-media-info">
            <div className="re0-media-heading">
              <h1 className="re0-media-title">
                {title}
                {year && <span className="re0-media-year">({year})</span>}
              </h1>
              {originalTitle && <p className="re0-media-orig-title">原始标题：{originalTitle}</p>}
            </div>

            {/* Collection tag / Series tag */}
            {isSeries && (
              <div className="re0-media-tags-row">
                <span className="re0-collection-tag">
                  <FolderKanban size={14} />
                  <span>系列资源 · {item.episode_count ? `${item.episode_count} 集` : "多季集合"}</span>
                </span>
              </div>
            )}

            {/* Ratings & Match Confidence */}
            <div className="re0-ratings-bar">
              {rating > 0 && (
                <div className="re0-rating-pill" title={`TMDB 评分：${rating.toFixed(1)}`}>
                  <Star size={14} fill="#ffbf00" color="#ffbf00" />
                  <b>{rating.toFixed(1)}</b>
                  <span>/ 10</span>
                  <small>TMDB</small>
                </div>
              )}
              {confidence && (
                <div className="re0-confidence-pill">
                  <Sparkles size={13} />
                  <span>匹配度 {confidence}%</span>
                </div>
              )}
              <div className="re0-media-type-pill">
                {displayMediaType(item.media_type)}
              </div>
            </div>

            {/* Genres Chips */}
            {genres.length > 0 && (
              <div className="re0-genre-chips">
                {genres.map((g) => (
                  <span key={g.id || g.name} className="re0-genre-chip">
                    {g.name}
                  </span>
                ))}
              </div>
            )}

            {/* Facts: Country · Release Date */}
            <div className="re0-meta-facts">
              {countries.length > 0 && (
                <span>
                  {countries.map((c) => {
                    try { return regionNames.of(c) || c; } catch { return c; }
                  }).join(" / ")}
                </span>
              )}
              {item.release_date && <span>上映日期：{formatDate(item.release_date)}</span>}
              <span>共收录 {item.source_count || item.sources?.length || 0} 个网盘来源</span>
            </div>

            {/* Overview / Synopsis */}
            {item.overview && (
              <div className="re0-media-overview">
                <p>{item.overview}</p>
              </div>
            )}

            {/* Action buttons */}
            <div className="re0-media-actions">
              {tmdbConfigured && (
                <button
                  type="button"
                  className="re0-btn re0-btn-ghost"
                  onClick={handleSearchCandidatesClick}
                  disabled={candidateSearching}
                >
                  <Search size={14} className={candidateSearching ? "spin" : ""} />
                  <span>{candidateSearching ? "搜索候选..." : "重新检索 TMDB"}</span>
                </button>
              )}
              {item.tmdb_id && (
                <button
                  type="button"
                  className="re0-btn re0-btn-ghost danger"
                  onClick={async () => {
                    if (confirm("确定解除当前 TMDB 关联吗？")) {
                      await onUnlink?.();
                      await loadDetail();
                    }
                  }}
                >
                  <Unlink size={14} />
                  <span>解除关联</span>
                </button>
              )}
            </div>
          </div>
        </section>

        {/* TMDB Candidates Section (Expandable) */}
        {showCandidates && item.candidates?.length > 0 && (
          <section className="re0-candidates-box">
            <div className="re0-candidates-head">
              <h3>TMDB 匹配候选 ({item.candidates.length})</h3>
              <button type="button" onClick={() => setShowCandidates(false)}>
                <X size={16} />
              </button>
            </div>
            <div className="re0-candidates-list">
              {item.candidates.map((c) => (
                <div key={c.id} className="re0-candidate-item">
                  <div className="re0-candidate-info">
                    <strong>{c.title} ({c.year || "未知"})</strong>
                    <small>置信度: {Math.round(c.score * 100)}% · TMDB ID: {c.tmdb_id}</small>
                  </div>
                  <button
                    type="button"
                    className="re0-btn re0-btn-primary small"
                    onClick={async () => {
                      await onLink?.(c);
                      setShowCandidates(false);
                      await loadDetail();
                    }}
                  >
                    关联此项
                  </button>
                </div>
              ))}
            </div>
          </section>
        )}

        {/* ================================================================= */}
        {/* CORE SECTION: 网盘来源 (Cloud Drives & Resource Cards Grid)        */}
        {/* ================================================================= */}
        <section className="re0-resources-section">
          <div className="re0-section-heading">
            <div className="re0-section-title-wrap">
              <h2 className="re0-section-title">网盘</h2>
              <span className="re0-section-count">
                {filteredSources.length} 条可用来源
              </span>
            </div>
          </div>

          {/* Provider Tabs (Horizontal Capsules) */}
          <div className="re0-provider-tabs-container">
            <div className="re0-provider-tabs" role="tablist" aria-label="网盘来源分类">
              {providerTabs.map((tab) => {
                const isSelected = activeTab === tab.id;
                return (
                  <button
                    key={tab.id}
                    type="button"
                    role="tab"
                    className={`re0-provider-tab ${isSelected ? "active" : ""}`}
                    aria-selected={isSelected}
                    onClick={() => setActiveTab(tab.id)}
                  >
                    <span className="re0-tab-icon">
                      {tab.id === "all" ? (
                        <Film size={15} />
                      ) : (
                        <ProviderIcon providerId={tab.id} size={15} />
                      )}
                    </span>
                    <span className="re0-tab-name">{tab.label}</span>
                    <b className="re0-tab-badge">{tab.count}</b>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Secondary Sub-filters (Resolution & Status) */}
          <div className="re0-resource-subfilters">
            <label className="re0-subfilter">
              <span>分辨率</span>
              <select
                value={selectedQuality}
                onChange={(e) => setSelectedQuality(e.target.value)}
              >
                <option value="all">全部分辨率</option>
                <option value="2160P">4K / 2160P</option>
                <option value="1080P">1080P</option>
                <option value="720P">720P</option>
              </select>
            </label>

            <label className="re0-subfilter">
              <span>状态</span>
              <select
                value={selectedStatus}
                onChange={(e) => setSelectedStatus(e.target.value)}
              >
                <option value="all">全部状态</option>
                <option value="available">仅可用</option>
              </select>
            </label>
          </div>

          {/* 3-Column Resource Cards Grid */}
          {!filteredSources.length ? (
            <div className="re0-no-resources">
              <p>当前网盘或筛选条件下暂无来源。</p>
            </div>
          ) : (
            <div className="re0-resource-grid">
              {filteredSources.map((source) => {
                const facts = extractSourceFacts(source);
                const titleText = source.raw_label || source.filename || "网盘资源分享";
                const isChecking = checkingId === source.id;
                const providerId = source.source_type === "ed2k" ? "ed2k" : (source.provider || source.source_type);
                const isAvailable = source.availability_status === "available";

                return (
                  <div
                    key={source.id}
                    className="re0-resource-card"
                    role="article"
                    tabIndex={0}
                    onClick={() => source.url && window.open(source.url, "_blank", "noopener,noreferrer")}
                  >
                    {/* Card Header: Publisher, Avatar, Date, Tag */}
                    <div className="re0-card-header">
                      <div className="re0-publisher-avatar">
                        <ProviderIcon providerId={providerId} size={20} />
                      </div>
                      <div className="re0-publisher-meta">
                        <div className="re0-publisher-name-row">
                          <strong className="re0-publisher-name">
                            {source.channel_name || PROVIDER_NAMES[providerId] || "网盘来源"}
                          </strong>
                        </div>
                        <span className="re0-publish-date">
                          {source.published_at ? `收录于 ${formatDate(source.published_at)}` : "已入库"}
                        </span>
                      </div>
                      <span className={`re0-status-pill ${isAvailable ? "available" : "pending"}`}>
                        {isAvailable ? "有效" : "待查"}
                      </span>
                    </div>

                    {/* Card Middle: Title / Remark */}
                    <div className="re0-card-remark">
                      <p title={titleText}>{titleText}</p>
                    </div>

                    {/* Dotted Divider */}
                    <div className="re0-card-dotted-divider" aria-hidden="true" />

                    {/* Card Bottom: 4 Technical Specification Keyword Pills (Card Facts) */}
                    <div className="re0-card-facts">
                      {facts.map((fact, idx) => {
                        const Icon = fact.Icon;
                        return (
                          <span
                            key={idx}
                            className={`re0-card-fact re0-card-fact--${fact.type}`}
                            title={fact.label}
                          >
                            <span className="re0-fact-icon">
                              <Icon size={12} />
                            </span>
                            <span>{fact.label}</span>
                          </span>
                        );
                      })}
                      {facts.length === 0 && (
                        <span className="re0-card-fact">
                          <span className="re0-fact-icon"><Film size={12} /></span>
                          <span>标准片源</span>
                        </span>
                      )}
                    </div>

                    {/* Card Action Bar */}
                    <div
                      className="re0-card-actions"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <button
                        type="button"
                        className="re0-card-btn"
                        onClick={() => handleCopyLink(source.url)}
                        title="复制网盘分享链接"
                      >
                        <Clipboard size={13} />
                        <span>复制链接</span>
                      </button>

                      {source.url && (
                        <a
                          href={source.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="re0-card-btn"
                          title="在浏览器中直接打开分享链接"
                        >
                          <ExternalLink size={13} />
                          <span>打开网盘</span>
                        </a>
                      )}

                      <button
                        type="button"
                        className="re0-card-btn icon-only"
                        disabled={isChecking}
                        onClick={() => handleCheck(source.id)}
                        title="重新检查网盘链接有效性"
                      >
                        <RefreshCw size={13} className={isChecking ? "spin" : ""} />
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </section>
      </div>
    </>
  )}
</div>
);
}
