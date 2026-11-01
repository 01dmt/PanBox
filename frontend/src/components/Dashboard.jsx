import { useCallback, useEffect, useRef, useState } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowDownRight,
  ArrowRight,
  ArrowUpRight,
  ChevronRight,
  Clock3,
  Database,
  Film,
  Link2,
  LoaderCircle,
  RefreshCw,
  X,
} from "lucide-react";
import { getDashboardOverview, getIngestionRecord } from "../api";
import { formatBytes, formatDateTime, formatNumber, maskedSource } from "../lib/format";

const EVENT_STATUS = {
  imported: { label: "已入库", tone: "ok" },
  duplicate: { label: "重复", tone: "info" },
  ignored: { label: "已忽略", tone: "muted" },
  failed: { label: "失败", tone: "bad" },
  pending_validation: { label: "等待校验", tone: "warn" },
  queued: { label: "排队中", tone: "warn" },
  rejected: { label: "已拒绝", tone: "bad" },
};

const LINK_STATUS = {
  inserted: { label: "已入库", tone: "ok" },
  duplicate: { label: "重复", tone: "info" },
  validated: { label: "有效", tone: "ok" },
  pending_validation: { label: "等待校验", tone: "warn" },
  invalid: { label: "失效 / 密码错误", tone: "bad" },
  error: { label: "校验错误", tone: "bad" },
  extracted: { label: "已提取", tone: "muted" },
};

const PROVIDERS = {
  "115": { label: "115 网盘", color: "var(--accent)" },
  "123": { label: "123 云盘", color: "var(--blue)" },
  guangya: { label: "光鸭", color: "var(--green)" },
  ed2k: { label: "ED2K", color: "#8c8ca2" },
};

const RANGES = [
  { value: 7, label: "7 天" },
  { value: 30, label: "30 天" },
];

function formatPercent(value, digits = 1) {
  return typeof value === "number" && Number.isFinite(value) ? `${(value * 100).toFixed(digits)}%` : "—";
}

function formatCount(value) {
  const number = Number(value || 0);
  return number >= 10000 ? new Intl.NumberFormat("zh-CN", { notation: "compact", maximumFractionDigits: 1 }).format(number) : formatNumber(number);
}

function dayLabel(value) {
  const [, month, day] = String(value || "").split("-");
  return month && day ? `${Number(month)}/${Number(day)}` : value;
}

function clockLabel(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false }).format(date);
}

function relativeTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const seconds = (date.getTime() - Date.now()) / 1000;
  const abs = Math.abs(seconds);
  const formatter = new Intl.RelativeTimeFormat("zh-CN", { numeric: "auto" });
  if (abs < 60) return "刚刚";
  if (abs < 3600) return formatter.format(Math.round(seconds / 60), "minute");
  if (abs < 86400) return formatter.format(Math.round(seconds / 3600), "hour");
  if (abs < 86400 * 7) return formatter.format(Math.round(seconds / 86400), "day");
  return formatDateTime(value);
}

function maskAccessCodes(value) {
  return String(value || "").replace(/((?:password|pwd|code)=)[^&#\s]+/gi, "$1••••");
}

function niceScale(maxValue) {
  if (!Number.isFinite(maxValue) || maxValue <= 0) return { top: 4, step: 1 };
  const rough = maxValue / 4;
  const power = 10 ** Math.floor(Math.log10(rough));
  const candidates = [1, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10];
  const step = (candidates.find((candidate) => candidate * power >= rough) ?? 10) * power;
  return { top: step * 4, step };
}

function StatusBadge({ status, map = EVENT_STATUS }) {
  const meta = map[status] || { label: status || "未知", tone: "muted" };
  return <span className={`dash-status ${meta.tone}`}>{meta.label}</span>;
}

function ChannelAvatar({ url, name }) {
  const [broken, setBroken] = useState(false);
  if (!url || broken) {
    return <span className="dash-avatar fallback" aria-hidden="true">{(name || "?").slice(0, 1)}</span>;
  }
  return <img className="dash-avatar" src={url} alt="" loading="lazy" onError={() => setBroken(true)} />;
}

function Sparkline({ rows }) {
  const values = rows.map((row) => row.imported || 0);
  const max = Math.max(1, ...values);
  const total = values.reduce((sum, value) => sum + value, 0);
  const points = values.map((value, index) => [
    (index / Math.max(1, values.length - 1)) * 240,
    56 - (value / max) * 48,
  ]);
  const line = points.map(([x, y], index) => `${index ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const area = `${line} L240,60 L0,60 Z`;
  return (
    <div className="dash-spark">
      <svg viewBox="0 0 240 60" preserveAspectRatio="none" role="img" aria-label={`近 ${rows.length} 天入库走势`}>
        <defs>
          <linearGradient id="dashSparkFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.32" />
            <stop offset="100%" stopColor="var(--accent)" stopOpacity="0" />
          </linearGradient>
        </defs>
        <path d={area} fill="url(#dashSparkFill)" />
        <path d={line} fill="none" stroke="var(--accent)" strokeWidth="1.6" vectorEffect="non-scaling-stroke" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <div className="dash-spark-labels"><span>近 {rows.length} 天入库走势</span><strong>{formatNumber(total)} 条</strong></div>
    </div>
  );
}

function TrendChart({ rows }) {
  const [hover, setHover] = useState(null);
  const maxReceived = Math.max(0, ...rows.map((row) => row.received || 0));
  const { top, step } = niceScale(maxReceived);
  const ticks = [0, 1, 2, 3, 4].map((index) => index * step);
  const labelEvery = Math.max(1, Math.ceil(rows.length / 6));
  const hovered = hover == null ? null : rows[hover];
  const dense = rows.length > 7;
  const segments = [
    { key: "imported", label: "入库", color: "var(--green)" },
    { key: "duplicate", label: "重复", color: "var(--blue)" },
    { key: "ignored", label: "忽略", color: "var(--subtle)" },
    { key: "failed", label: "失败", color: "var(--coral)" },
    { key: "pending", label: "待校验", color: "var(--amber)" },
  ];
  const summaryFor = (row) => `接收 ${formatNumber(row.received)}，入库 ${formatNumber(row.imported)}，重复 ${formatNumber(row.duplicate)}，忽略 ${formatNumber(row.ignored)}，失败 ${formatNumber(row.failed)}，待校验 ${formatNumber(row.pending)}`;

  if (!maxReceived) {
    return <div className="dash-empty"><Activity size={20} /><span>所选区间还没有入库记录。</span></div>;
  }

  return (
    <div className="dash-trend" onPointerLeave={() => setHover(null)}>
      <div className={`dash-trend-y ${dense ? "dense" : ""}`} aria-hidden="true">
        {[...ticks].reverse().map((tick) => <span key={tick}>{formatCount(tick)}</span>)}
      </div>
      <div className="dash-trend-plot">
        <div className="dash-trend-grid" aria-hidden="true">
          {ticks.map((tick) => <i key={tick} style={{ bottom: `${(tick / top) * 100}%` }} />)}
        </div>
        <div className={`dash-trend-cols ${dense ? "dense" : ""}`} role="img" aria-label={`近 ${rows.length} 天每日接收与处理状态构成`}>
          {rows.map((row, index) => {
            const known = segments.reduce((sum, segment) => sum + (row[segment.key] || 0), 0);
            const other = Math.max(0, (row.received || 0) - known);
            return (
              <span
                key={row.date}
                className={`dash-trend-col ${hover === index ? "active" : ""}`}
                onPointerEnter={() => setHover(index)}
                title={`${row.date}：${summaryFor(row)}`}
              >
                <span className="dash-trend-stack">
                  {segments.map((segment) => row[segment.key] ? (
                    <i key={segment.key} className={segment.key} style={{ height: `${((row[segment.key] || 0) / top) * 100}%`, background: segment.color }} />
                  ) : null)}
                  {other ? <i className="other" style={{ height: `${(other / top) * 100}%` }} /> : null}
                </span>
              </span>
            );
          })}
        </div>
        {hovered && (
          <div className={`dash-trend-tip ${hover > rows.length - 3 ? "flip" : ""}`} style={{ left: `${((hover + 0.5) / rows.length) * 100}%` }}>
            <strong>{hovered.date} · 接收 {formatNumber(hovered.received)}</strong>
            {segments.map((segment) => (
              <span key={segment.key}><i style={{ background: segment.color }} />{segment.label}<b>{formatNumber(hovered[segment.key] || 0)}</b></span>
            ))}
          </div>
        )}
      </div>
      <div className={`dash-trend-x ${dense ? "dense" : ""}`} aria-hidden="true">
        {rows.map((row, index) => (
          <span key={row.date}>{index % labelEvery === 0 || index === rows.length - 1 ? dayLabel(row.date) : ""}</span>
        ))}
      </div>
    </div>
  );
}

function MatchDonut({ media }) {
  const total = media.total || 0;
  const matched = Math.min(media.matched || 0, total);
  const review = Math.min(media.review || 0, Math.max(0, total - matched));
  const other = Math.max(0, total - matched - review);
  if (!total) return <div className="dash-empty"><Film size={20} /><span>暂无作品数据。</span></div>;

  const segments = [
    { key: "matched", label: "已匹配", value: matched, color: "var(--green)" },
    { key: "review", label: "待确认", value: review, color: "var(--amber)" },
    { key: "other", label: "其他待处理", value: other, color: "var(--subtle)" },
  ];
  let cursor = 0;
  const gradient = segments.map((segment) => {
    const from = (cursor / total) * 100;
    cursor += segment.value;
    const to = (cursor / total) * 100;
    return `${segment.color} ${from.toFixed(2)}% ${to.toFixed(2)}%`;
  }).join(", ");

  return (
    <div className="dash-donut-layout">
      <div className="dash-donut" style={{ background: `conic-gradient(${gradient})` }} role="img" aria-label={`共 ${formatNumber(total)} 部作品，已匹配 ${formatNumber(matched)} 部`}>
        <div className="dash-donut-center">
          <strong>{formatPercent(media.match_rate)}</strong>
          <span>TMDB 匹配率</span>
        </div>
      </div>
      <div className="dash-legend-list">
        {segments.map((segment) => (
          <div key={segment.key} className="dash-legend-row">
            <i style={{ background: segment.color }} />
            <span>{segment.label}</span>
            <strong>{formatNumber(segment.value)}</strong>
            <small>{total ? `${Math.round((segment.value / total) * 100)}%` : "—"}</small>
          </div>
        ))}
        <p className="dash-legend-note">共 {formatNumber(total)} 部作品 · 电影 {formatNumber(media.movies)} · 剧集 {formatNumber(media.tv)}</p>
      </div>
    </div>
  );
}

function ProviderBreakdown({ sources, backlog, onOpenFailed }) {
  const total = sources.total || 0;
  return (
    <section className="dash-panel" aria-labelledby="dash-provider-title">
      <header className="dash-panel-head">
        <div className="dash-panel-title">
          <h2 id="dash-provider-title">来源构成</h2>
          <p>全部 {formatNumber(total)} 条来源 · 标注体积 {formatBytes(sources.bytes)}</p>
        </div>
        <span className="dash-pill">今日 +{formatNumber(sources.added_today)}</span>
      </header>
      {total ? (
        <div className="dash-provider-list">
          {sources.providers.map((provider) => {
            const meta = PROVIDERS[provider.id] || { label: provider.id, color: "var(--subtle)" };
            const share = total ? (provider.count / total) * 100 : 0;
            return (
              <div className="dash-provider" key={provider.id}>
                <span className="dash-provider-name"><i style={{ background: meta.color }} />{meta.label}</span>
                <span className="dash-provider-track"><i style={{ width: `${Math.max(1.5, share)}%`, background: meta.color }} /></span>
                <span className="dash-provider-value"><strong>{formatNumber(provider.count)}</strong><small>{share.toFixed(1)}%</small></span>
              </div>
            );
          })}
        </div>
      ) : <div className="dash-empty"><Database size={20} /><span>还没有来源记录。</span></div>}
      <footer className="dash-panel-foot">
        <span><i className="dot-ok" />已校验 {formatNumber(backlog.checked_links)}</span>
        <span><i className="dot-bad" />失效 {formatNumber(backlog.invalid_links)}</span>
        <button type="button" className="dash-link-btn" onClick={onOpenFailed}>处理失败记录 <ArrowRight size={13} /></button>
      </footer>
    </section>
  );
}

function ChannelsPanel({ channels, days }) {
  return (
    <section className="dash-panel" aria-labelledby="dash-channel-title">
      <header className="dash-panel-head">
        <div className="dash-panel-title">
          <h2 id="dash-channel-title">频道贡献</h2>
          <p>近 {days} 天接收量 Top {Math.min(6, channels.length) || 6}</p>
        </div>
        <span className="dash-pill">{channels.length} 个活跃频道</span>
      </header>
      {channels.length ? (
        <div className="dash-channel-list">
          {channels.map((channel) => (
            <div className="dash-channel-row" key={channel.id}>
              <ChannelAvatar url={channel.avatar_url} name={channel.name} />
              <div className="dash-channel-main">
                <strong title={channel.name}>{channel.name}</strong>
                <small>{channel.username ? `@${channel.username}` : channel.id} · {relativeTime(channel.last_received_at)}</small>
              </div>
              <div className="dash-channel-stats">
                <strong>{formatNumber(channel.received)}</strong>
                <small>入库 {formatNumber(channel.imported)}</small>
              </div>
            </div>
          ))}
        </div>
      ) : <div className="dash-empty"><Link2 size={20} /><span>所选区间没有频道活动。</span></div>}
    </section>
  );
}

function RecentPanel({ recent, onSelect, onOpenAll }) {
  return (
    <section className="dash-panel dash-recent" aria-labelledby="dash-recent-title">
      <header className="dash-panel-head">
        <div className="dash-panel-title">
          <h2 id="dash-recent-title">最近入库</h2>
          <p>最新 {recent.length} 条接收记录，点击查看原文与链接校验结果</p>
        </div>
        <button type="button" className="dash-link-btn" onClick={onOpenAll}>全部入库记录 <ArrowRight size={13} /></button>
      </header>
      {recent.length ? (
        <div className="dash-recent-table">
          <div className="dash-recent-head" aria-hidden="true">
            <span>时间</span><span>频道</span><span>媒体</span><span>状态</span><span>来源</span><span />
          </div>
          {recent.map((record) => (
            <button type="button" className="dash-recent-row" key={record.id} onClick={() => onSelect(record)} title={record.error || undefined}>
              <span className="r-time"><Clock3 size={12} />{clockLabel(record.received_at)}</span>
              <span className="r-channel">{record.channel_name}</span>
              <span className="r-media">
                {record.media.length
                  ? record.media.map((item) => `${item.title}${item.year ? `（${item.year}）` : ""}`).join("、")
                  : "未识别媒体"}
              </span>
              <span className="r-status"><StatusBadge status={record.status} /></span>
              <span className="r-links">{record.links?.total ? `${record.links.total} 条` : "—"}</span>
              <ChevronRight className="r-arrow" size={14} />
            </button>
          ))}
        </div>
      ) : <div className="dash-empty"><Clock3 size={20} /><span>还没有接收记录。</span></div>}
    </section>
  );
}

function RecordDetail({ record, onClose, onOpenImports }) {
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const dialogRef = useRef(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError("");
    getIngestionRecord(record.id)
      .then((payload) => { if (alive) setDetail(payload); })
      .catch((nextError) => { if (alive) setError(nextError?.message || "详情加载失败"); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [record.id]);

  useEffect(() => {
    const onKeyDown = (event) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    dialogRef.current?.focus();
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return (
    <div className="dash-modal-backdrop" onPointerDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="dash-modal" role="dialog" aria-modal="true" aria-labelledby="dash-detail-title" tabIndex={-1} ref={dialogRef}>
        <header className="dash-modal-head">
          <div>
            <span className="dash-kicker">INGESTION RECORD</span>
            <h2 id="dash-detail-title">{record.channel_name}</h2>
            <p>{formatDateTime(record.received_at)} · {record.service} · {record.id}</p>
          </div>
          <div className="dash-modal-actions">
            <StatusBadge status={record.status} />
            <button type="button" className="dash-icon-btn" onClick={onClose} aria-label="关闭详情"><X size={17} /></button>
          </div>
        </header>

        {loading ? (
          <div className="dash-modal-loading"><LoaderCircle size={18} className="spin" />正在读取记录详情…</div>
        ) : error ? (
          <div className="dash-modal-error">{error}</div>
        ) : (
          <>
            <div className="dash-detail-grid">
              <div><span>来源频道</span><strong>{detail.channel_name || "—"}</strong></div>
              <div><span>渠道</span><strong>{detail.service || "—"}</strong></div>
              <div><span>消息 ID</span><strong>{detail.message_id || "—"}</strong></div>
              <div><span>接收时间</span><strong>{formatDateTime(detail.received_at)}</strong></div>
              <div><span>处理时间</span><strong>{detail.processed_at ? formatDateTime(detail.processed_at) : "—"}</strong></div>
              <div><span>提取来源</span><strong>{formatNumber(detail.links?.length || 0)} 条</strong></div>
            </div>

            <div className="dash-detail-block">
              <h3>识别媒体</h3>
              {record.media.length ? (
                <div className="dash-media-chips">
                  {record.media.map((item) => <span key={item.id}>{item.title}{item.year ? `（${item.year}）` : ""}</span>)}
                </div>
              ) : <p className="dash-muted">没有识别出媒体。</p>}
            </div>

            <div className="dash-detail-block">
              <h3>来源链接与校验</h3>
              {detail.links?.length ? (
                <div className="dash-link-list">
                  {detail.links.map((link) => (
                    <div className="dash-link-row" key={link.id || link.source_key}>
                      <span className="dash-provider-tag" style={{ "--tag-color": (PROVIDERS[link.provider] || {}).color || "var(--subtle)" }}>
                        {(PROVIDERS[link.provider] || {}).label || link.provider}
                      </span>
                      <span className="dash-link-url" title={maskedSource(link.url)}>{maskedSource(link.url)}</span>
                      <StatusBadge status={link.status} map={LINK_STATUS} />
                      {link.error || link.validation_error ? <small className="dash-link-error">{link.error || link.validation_error}</small> : null}
                    </div>
                  ))}
                </div>
              ) : <p className="dash-muted">这条消息没有提取到受支持的来源链接。</p>}
            </div>

            <div className="dash-detail-block">
              <h3>原始正文</h3>
              <pre className="dash-raw">{maskAccessCodes(detail.raw_text) || "（无正文）"}</pre>
            </div>

            {detail.error ? <div className="dash-detail-error"><AlertTriangle size={14} />{detail.error}</div> : null}

            <footer className="dash-modal-foot">
              <button type="button" className="dash-btn ghost" onClick={onClose}>关闭</button>
              <button type="button" className="dash-btn primary" onClick={() => { onClose(); onOpenImports(record.status); }}>
                在入库记录中查看 <ArrowRight size={14} />
              </button>
            </footer>
          </>
        )}
      </section>
    </div>
  );
}

function DashboardSkeleton() {
  return (
    <main className="dash-page" aria-busy="true" aria-label="仪表盘加载中">
      <div className="dash-top">
        <div><span className="dash-kicker">OVERVIEW</span><h1>仪表盘</h1><p>正在汇总本地入库与资源数据…</p></div>
      </div>
      <div className="dash-hero-layout">
        <div className="dash-skeleton hero" />
        <div className="dash-kpis">
          <div className="dash-skeleton kpi" /><div className="dash-skeleton kpi" /><div className="dash-skeleton kpi" />
        </div>
      </div>
      <div className="dash-grid dash-grid-main">
        <div className="dash-skeleton panel" /><div className="dash-skeleton panel" />
      </div>
    </main>
  );
}

export default function Dashboard({ onPending, onReview, onResources, onImports }) {
  const [range, setRange] = useState(7);
  const [autoRefresh, setAutoRefresh] = useState(30);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [updatedAt, setUpdatedAt] = useState(null);
  const [detailRecord, setDetailRecord] = useState(null);
  const requestRef = useRef(null);

  const load = useCallback(async ({ silent = false } = {}) => {
    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    if (!silent) setLoading(true);
    setRefreshing(true);
    try {
      const payload = await getDashboardOverview(range, controller.signal);
      if (controller.signal.aborted) return;
      if (!payload?.today || !Array.isArray(payload.trend) || !payload.media || !payload.sources) {
        throw new Error("仪表盘数据格式异常，请重启服务后重试。");
      }
      setData(payload);
      setError("");
      setUpdatedAt(new Date());
    } catch (nextError) {
      if (!controller.signal.aborted && nextError?.name !== "AbortError") {
        setError(nextError?.message || "仪表盘数据加载失败。");
      }
    } finally {
      if (!controller.signal.aborted) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, [range]);

  useEffect(() => {
    load();
    return () => requestRef.current?.abort();
  }, [load]);

  useEffect(() => {
    if (!autoRefresh) return undefined;
    const timer = window.setInterval(() => load({ silent: true }), autoRefresh * 1000);
    return () => window.clearInterval(timer);
  }, [autoRefresh, load]);

  if (!data && loading) return <DashboardSkeleton />;
  if (!data && error) {
    return (
      <main className="dash-page" aria-label="仪表盘">
        <div className="dash-top"><div><span className="dash-kicker">OVERVIEW</span><h1>仪表盘</h1><p>无法读取仪表盘数据。</p></div></div>
        <div className="dash-load-error" role="alert">
          <AlertTriangle size={20} />
          <div><strong>数据加载失败</strong><p>{error}</p></div>
          <button type="button" className="dash-btn primary" onClick={() => load()}>重新加载</button>
        </div>
      </main>
    );
  }

  const today = data.today;
  const yesterday = data.yesterday;
  const media = data.media;
  const sources = data.sources;
  const backlog = data.backlog;
  const trend = data.trend;
  const rangeReceived = trend.reduce((sum, row) => sum + (row.received || 0), 0);
  const rangeImported = trend.reduce((sum, row) => sum + (row.imported || 0), 0);
  const rangeFailed = trend.reduce((sum, row) => sum + (row.failed || 0), 0);
  const importedDelta = yesterday.imported ? (today.imported - yesterday.imported) / yesterday.imported : null;
  const ratePercent = typeof today.success_rate === "number" ? Math.max(0, Math.min(100, today.success_rate * 100)) : 0;

  return (
    <main className="dash-page" aria-label="仪表盘">
      <header className="dash-top">
        <div>
          <span className="dash-kicker">OVERVIEW / 接入与资源</span>
          <h1>仪表盘</h1>
          <p>今日入库、链接校验与 TMDB 匹配，一屏掌握处理链路。</p>
        </div>
        <div className="dash-tools">
          <span className="dash-updated">{updatedAt ? `更新于 ${clockLabel(updatedAt)}` : "尚未更新"}</span>
          <label className="dash-auto">自动刷新
            <select value={autoRefresh} onChange={(event) => setAutoRefresh(Number(event.target.value))}>
              <option value={0}>关闭</option>
              <option value={30}>30 秒</option>
              <option value={60}>60 秒</option>
            </select>
          </label>
          <button type="button" className="dash-icon-btn" onClick={() => load()} aria-label="立即刷新" title="立即刷新">
            <RefreshCw size={15} className={refreshing ? "spin" : ""} />
          </button>
        </div>
      </header>

      {error ? <div className="dash-inline-error" role="alert"><AlertTriangle size={14} />{error}<button type="button" onClick={() => load()}>重试</button></div> : null}

      <div className="dash-hero-layout">
        <article className="dash-hero">
          <div className="dash-hero-body">
            <div>
              <span className="dash-hero-label">今日入库</span>
              <div className="dash-hero-number">
                <strong>{formatNumber(today.imported)}</strong>
                <span>条</span>
                {importedDelta == null ? (
                  <em className="dash-delta neutral">昨日同期无数据</em>
                ) : (
                  <em className={`dash-delta ${importedDelta >= 0 ? "up" : "down"}`}>
                    {importedDelta >= 0 ? <ArrowUpRight size={11} /> : <ArrowDownRight size={11} />}
                    {Math.abs(importedDelta * 100).toFixed(1)}%
                    <span>较昨日同期</span>
                  </em>
                )}
              </div>
              <p className="dash-hero-sub">接收 {formatNumber(today.received)} 条 · 已处理 {formatNumber(today.processed)} 条 · 待处理 {formatNumber(today.pending)} 条</p>
              <div className="dash-chips">
                <span className="dash-chip"><i className="dot-received" />接收<strong>{formatNumber(today.received)}</strong></span>
                <span className="dash-chip info"><i />重复<strong>{formatNumber(today.duplicate)}</strong></span>
                <span className="dash-chip"><i className="dot-ignored" />忽略<strong>{formatNumber(today.ignored)}</strong></span>
                <span className={`dash-chip ${today.failed ? "bad" : ""}`}><i />失败<strong>{formatNumber(today.failed)}</strong></span>
                <span className={`dash-chip ${today.pending ? "warn" : ""}`}><i />待校验<strong>{formatNumber(today.pending)}</strong></span>
              </div>
            </div>
            <div className="dash-hero-rate">
              <span>处理成功率</span>
              <strong>{formatPercent(today.success_rate)}</strong>
              <div className="dash-meter" role="img" aria-label={`处理成功率 ${formatPercent(today.success_rate)}`}><i style={{ width: `${ratePercent}%` }} /></div>
              <small>昨日同期 {formatPercent(yesterday.success_rate)}</small>
            </div>
          </div>
          <Sparkline rows={trend.slice(-7)} />
        </article>

        <div className="dash-kpis">
          <button type="button" className="dash-kpi" onClick={onPending}>
            <span className="dash-kpi-head"><span className="dash-kpi-icon amber"><Film size={16} /></span><span>待匹配作品</span><ChevronRight size={13} /></span>
            <strong>{formatNumber(media.unmatched)}</strong>
            <small>其中 {formatNumber(media.review)} 条待确认</small>
          </button>
          <button type="button" className="dash-kpi" onClick={onResources}>
            <span className="dash-kpi-head"><span className="dash-kpi-icon green"><Link2 size={16} /></span><span>来源总量</span><ChevronRight size={13} /></span>
            <strong>{formatNumber(sources.total)}</strong>
            <small>今日 +{formatNumber(sources.added_today)} · {formatBytes(sources.bytes)}</small>
          </button>
          <button type="button" className="dash-kpi" onClick={() => onImports("failed")}>
            <span className="dash-kpi-head"><span className="dash-kpi-icon coral"><AlertTriangle size={16} /></span><span>历史失败记录</span><ChevronRight size={13} /></span>
            <strong>{formatNumber(backlog.failed_events)}</strong>
            <small>失效链接 {formatNumber(backlog.invalid_links)} 条 · 点击排查</small>
          </button>
        </div>
      </div>

      <div className="dash-grid dash-grid-main">
        <section className="dash-panel" aria-labelledby="dash-trend-title">
          <header className="dash-panel-head">
            <div className="dash-panel-title">
              <h2 id="dash-trend-title">接收与入库趋势</h2>
              <p>近 {range} 天 · 按自然日汇总</p>
            </div>
            <div className="dash-segment" role="group" aria-label="趋势时间范围">
              {RANGES.map((option) => (
                <button key={option.value} type="button" className={range === option.value ? "active" : ""} aria-pressed={range === option.value} onClick={() => setRange(option.value)}>{option.label}</button>
              ))}
            </div>
          </header>
          <div className="dash-chart-summary">
            <span><i className="received" /><small>接收</small><strong>{formatNumber(rangeReceived)}</strong></span>
            <span><i className="imported" /><small>入库</small><strong>{formatNumber(rangeImported)}</strong></span>
            <span><i className="failed" /><small>失败</small><strong>{formatNumber(rangeFailed)}</strong></span>
            <span className="dash-chart-hint">悬停查看每日明细</span>
          </div>
          <TrendChart rows={trend} />
          <div className="dash-chart-legend">
            <span><i className="imported" />入库</span>
            <span><i className="duplicate" />重复</span>
            <span><i className="ignored" />忽略</span>
            <span><i className="failed" />失败</span>
            <span><i className="pending" />待校验</span>
          </div>
        </section>

        <section className="dash-panel" aria-labelledby="dash-match-title">
          <header className="dash-panel-head">
            <div className="dash-panel-title">
              <h2 id="dash-match-title">匹配进度</h2>
              <p>影视库 TMDB 关联状态</p>
            </div>
            <button type="button" className="dash-link-btn" onClick={onPending}>去处理 <ArrowRight size={13} /></button>
          </header>
          <MatchDonut media={media} />
          <footer className="dash-panel-foot">
            <span><i className="dot-ok" />已匹配 {formatNumber(media.matched)}</span>
            <span><i className="dot-warn" />待确认 {formatNumber(media.review)}</span>
            <button type="button" className="dash-link-btn" onClick={onReview}>审核待确认 <ArrowRight size={13} /></button>
          </footer>
        </section>
      </div>

      <div className="dash-grid dash-grid-secondary">
        <ProviderBreakdown sources={sources} backlog={backlog} onOpenFailed={() => onImports("failed")} />
        <ChannelsPanel channels={data.channels} days={range} />
      </div>

      <RecentPanel recent={data.recent} onSelect={setDetailRecord} onOpenAll={() => onImports("all")} />

      {detailRecord ? <RecordDetail record={detailRecord} onClose={() => setDetailRecord(null)} onOpenImports={onImports} /> : null}
    </main>
  );
}
