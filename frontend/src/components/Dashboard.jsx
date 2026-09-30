import { useEffect, useMemo, useRef, useState } from "react";
import { Activity, ArrowRight, CirclePause, Database, HardDrive, Link2, Play, RefreshCw, ShieldCheck, X } from "lucide-react";
import { formatBytes, formatNumber } from "../lib/format";
import { dashboardMock } from "../services/dashboardMock";

const TYPES = { success: "入库成功", ignored: "过滤忽略", duplicate: "重复合并", error: "格式错误" };
const ALL = "all";

function Metric({ icon: Icon, title, value, detail, tone = "teal" }) {
  return <article className="dash-metric"><span className={`metric-icon ${tone}`}><Icon size={19} /></span><div><span>{title}</span><strong>{value}</strong><small>{detail}</small></div></article>;
}

function TrendChart({ rows }) {
  const max = Math.max(1, ...rows.flatMap((row) => [row.received ?? 0, row.imported ?? 0]));
  const points = (key) => rows.map((row, i) => `${32 + (i * 520) / Math.max(1, rows.length - 1)},${170 - row[key] / max * 130}`).join(" ");
  return <div className="dash-trend"><svg viewBox="0 0 580 200" role="img" aria-label="演示数据：外部接收消息数与成功入库数趋势图" preserveAspectRatio="none">
    {[40, 83, 126, 170].map((y) => <line key={y} x1="32" x2="552" y1={y} y2={y} stroke="#e5eaed" />)}
    <polyline points={points("received")} fill="none" stroke="#087f7b" strokeWidth="3" strokeLinejoin="round" />
    <polyline points={points("imported")} fill="none" stroke="#287db0" strokeWidth="3" strokeLinejoin="round" />
  </svg><div className="dash-legend"><span><i style={{ background: "#087f7b" }} />接收消息</span><span><i style={{ background: "#287db0" }} />成功入库</span></div></div>;
}

export default function Dashboard({ stats, onPending }) {
  const [paused, setPaused] = useState(false);
  const [logPaused, setLogPaused] = useState(false);
  const [filter, setFilter] = useState(ALL);
  const [selectedLog, setSelectedLog] = useState(null);
  const [range, setRange] = useState(7);
  const [refresh, setRefresh] = useState(0);
  const [refreshRate, setRefreshRate] = useState(0);
  const [message, setMessage] = useState("");
  const [hiddenLogs, setHiddenLogs] = useState(false);
  const noticeTimer = useRef(null);
  const logs = useMemo(() => (hiddenLogs ? [] : dashboardMock.logs.filter((log) => filter === ALL || log.type === filter)), [filter, hiddenLogs]);
  useEffect(() => () => window.clearTimeout(noticeTimer.current), []);
  useEffect(() => {
    setHiddenLogs(false);
  }, [filter]);
  useEffect(() => {
    if (!refreshRate) return undefined;
    const timer = window.setInterval(() => setRefresh((value) => value + 1), refreshRate * 1000);
    return () => window.clearInterval(timer);
  }, [refreshRate]);
  useEffect(() => {
    if (!selectedLog) return undefined;
    const close = (event) => { if (event.key === "Escape") setSelectedLog(null); };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [selectedLog]);
  const notify = (value) => { setMessage(value); window.clearTimeout(noticeTimer.current); noticeTimer.current = window.setTimeout(() => setMessage(""), 3500); };
  const total = stats?.media?.total;
  const matched = stats?.media?.matched;
  const bytes = stats?.sources?.bytes;
  const matchedRate = total && Number.isFinite(matched) ? `${(matched / total * 100).toFixed(1)}%` : "—";
  const donut = dashboardMock.distribution.reduce((result, row) => {
    const start = result.offset;
    return { offset: start + row.value, stops: [...result.stops, `${row.color} ${start}% ${start + row.value}%`] };
  }, { offset: 0, stops: [] }).stops.join(", ");

  return <main className="dashboard-page" aria-label="仪表盘">
    <div className="dash-heading"><div><span className="dash-eyebrow">OVERVIEW / 接入与资源</span><h2>资源运营仪表盘</h2><p>从外部接收、解析匹配到资源缓存，一览处理链路。</p></div><span className="dash-demo-tag">演示模式 · 仅资产指标为真实数据</span></div>
    <div className="dash-alert" role="note"><Activity size={17} /><span>外部 TG 爬虫、Webhook / 队列、跨网盘巡检及转存尚未接入。以下接入日志、频道、曲线和来源分布均为示例；操作仅在本页模拟，不会调用后端。</span></div>
    <div className="dash-controls"><span className="dash-control-label">演示控制台</span><button type="button" className="button small" aria-pressed={paused} onClick={() => { setPaused(!paused); notify("仅切换演示接入状态；真实接入尚未配置。"); }}>{paused ? <Play size={15} /> : <CirclePause size={15} />}{paused ? "恢复接入（模拟）" : "暂停外部接收（模拟）"}</button><button type="button" className="button small" onClick={() => notify("全量巡检尚未实现；未发起任何网盘请求。") }><ShieldCheck size={15} />触发全量检测</button><label>自动刷新<select value={refreshRate} onChange={(event) => setRefreshRate(Number(event.target.value))}><option value={0}>关闭</option><option value={5}>5 秒（演示）</option><option value={15}>15 秒（演示）</option></select></label><span className="dash-refresh-mark">演示刷新 #{refresh}</span></div>
    {message && <div className="dash-feedback" role="status">{message}</div>}
    <section className="dash-metrics" aria-label="核心指标">
      <Metric icon={Database} title="影视库资产" value={total == null ? "—" : formatNumber(total)} detail={`TMDB 匹配率 ${matchedRate} · 无来源 ${formatNumber(stats?.media?.without_sources ?? 0)} · 待处理 ${formatNumber(stats?.media?.pending ?? 0)}`} />
      <Metric icon={Activity} title="外部接入流水" value="未接入" detail="今日接收 / 解析率 / 重复：暂无真实数据" tone="amber" />
      <Metric icon={Link2} title="网盘资源池" value={formatNumber(stats?.sources?.total ?? 0)} detail="存活率及今日新增：未检测" tone="blue" />
      <Metric icon={HardDrive} title="存储与缓存池" value={bytes ? formatBytes(bytes) : "暂无统计"} detail="来源标注文件大小合计，非本地磁盘占用；驱动未接入" tone="green" />
    </section>
    <div className="dash-split">
      <section className="dash-panel"><div className="dash-panel-head"><div><h3>外部接入与来源</h3><small>External source health</small></div><span className="dash-badge idle">未配置</span></div>
        <div className="dash-endpoint"><span className="dash-status-dot idle" />Webhook / 消息队列：未配置接入端点</div><p className="dash-panel-hint">以下频道仅展示数据结构样例，尚无真实推送。</p>
        {dashboardMock.channels.map((channel) => <div className="dash-channel" key={channel.id}><div><strong>{channel.name}</strong><small>最后推送示例 · {channel.last_received_at}</small></div><span>{channel.messages} 条 · 示例</span></div>)}
        <button type="button" className="dash-text-action" onClick={() => notify("未连接上游队列，无积压可清空。")}>清空上游积压 <ArrowRight size={14} /></button>
      </section>
      <section className="dash-panel"><div className="dash-panel-head"><div><h3>实时解析流水线</h3><small>Live ingestion stream · 示例</small></div><span className="dash-badge">样例日志</span></div>
        <div className="dash-log-tools"><label>日志类型<select value={filter} onChange={(event) => setFilter(event.target.value)}><option value={ALL}>全部类型</option>{Object.entries(TYPES).map(([key, value]) => <option key={key} value={key}>{value}</option>)}</select></label><button type="button" onClick={() => { setLogPaused(!logPaused); notify(logPaused ? "已恢复演示日志滚动状态。" : "已暂停演示日志滚动；真实推流尚未接入。"); }}>{logPaused ? "继续滚动" : "暂停滚动"}</button><button type="button" onClick={() => setHiddenLogs(true)}>清屏</button></div>
        <div className="dash-log-list" aria-label="示例日志">{logs.length ? logs.map((log) => <button type="button" className="dash-log-row" key={log.id} onClick={() => setSelectedLog(log)}><time>[{log.time}]</time><span className={`dash-log-type ${log.type}`}>[{TYPES[log.type]}]</span><span className="dash-log-summary">{log.summary}</span><ArrowRight size={14} /></button>) : <p className="dash-empty">暂无示例日志。切换筛选条件或刷新页面可重新查看。</p>}</div>
      </section>
    </div>
    <section className="dash-panel"><div className="dash-panel-head"><div><h3>任务处理流水线</h3><small>Pipeline queue status</small></div><span className="dash-badge idle">队列未连接</span></div><div className="dash-queues">{dashboardMock.queues.map((queue) => <div className="dash-queue" key={queue.id}><div className="dash-queue-title"><strong>{queue.label}</strong><span>{queue.state === "not_implemented" ? "尚未实现" : "无实时数据"}</span></div><div className="dash-progress" role="progressbar" aria-label={queue.label} aria-valuetext="未连接，暂无进度" /><small>{queue.id === "cleaning" ? <>待处理作品：{formatNumber(stats?.media?.pending ?? 0)} · <button type="button" className="dash-inline" onClick={onPending}>去待匹配 <ArrowRight size={12} /></button></> : queue.id === "health" ? "尚无跨网盘巡检任务" : "本项目尚无转存调度能力"}</small></div>)}</div></section>
    <div className="dash-split dash-analytics"><section className="dash-panel"><div className="dash-panel-head"><div><h3>接收与入库趋势</h3><small>示意曲线 · 非实际统计</small></div><div className="dash-range"><button type="button" className={range === 7 ? "active" : ""} onClick={() => setRange(7)}>7 天</button><button type="button" className={range === 30 ? "active" : ""} onClick={() => setRange(30)}>30 天</button></div></div><TrendChart rows={dashboardMock.trends.slice(-range)} /></section>
      <section className="dash-panel"><div className="dash-panel-head"><div><h3>网盘来源分布</h3><small>未来提供方规划示意 · 非实际占比</small></div><span className="dash-badge">示例</span></div><div className="dash-distribution"><div className="dash-donut" style={{ background: `conic-gradient(${donut})` }} role="img" aria-label="示例来源占比分布"><div>示例<span>100%</span></div></div><div className="dash-distribution-list">{dashboardMock.distribution.map((entry) => <div key={entry.label}><i style={{ background: entry.color }} /><span>{entry.label}</span><strong>{entry.value}%</strong></div>)}</div></div></section></div>
    {selectedLog && <div className="dash-modal-backdrop" onClick={() => setSelectedLog(null)}><section className="dash-modal" role="dialog" aria-modal="true" aria-label="示例接入消息详情" onClick={(event) => event.stopPropagation()}><header><div><strong>消息详情 · {TYPES[selectedLog.type]}</strong><small>演示 Payload · {selectedLog.source} · {selectedLog.time}</small></div><button type="button" className="icon-only" onClick={() => setSelectedLog(null)} aria-label="关闭详情"><X size={18} /></button></header><h4>原始 Payload（示例）</h4><pre>{JSON.stringify(selectedLog.raw, null, 2)}</pre><h4>清洗后结构（示例）</h4><pre>{JSON.stringify(selectedLog.parsed, null, 2)}</pre></section></div>}
  </main>;
}
