import { CheckCircle2, CircleHelp, Film, Link2, Layers3 } from "lucide-react";
import { formatNumber } from "../lib/format";


export default function StatsBar({ stats, activeView }) {
  const values = [
    {
      label: activeView === "library" ? "有资源作品" : "全部作品",
      value: activeView === "library" ? stats?.media?.with_sources : stats?.media?.total,
      meta: activeView === "library" ? `全部 ${formatNumber(stats?.media?.total)} · 无资源 ${formatNumber(stats?.media?.without_sources)}` : `电影 ${formatNumber(stats?.media?.movies)} · 剧集 ${formatNumber(stats?.media?.tv)}`,
      icon: Film,
      tone: "teal",
    },
    {
      label: "来源记录",
      value: stats?.sources?.total,
      meta: `115 ${formatNumber(stats?.sources?.share_115)} · ED2K ${formatNumber(stats?.sources?.ed2k)}`,
      icon: Link2,
      tone: "blue",
    },
    {
      label: "已匹配 TMDB",
      value: stats?.media?.matched,
      meta: `${stats?.media?.total ? Math.round((stats.media.matched / stats.media.total) * 100) : 0}% 完成`,
      icon: CheckCircle2,
      tone: "green",
    },
    activeView === "duplicates"
      ? {
          label: "多来源作品",
          value: stats?.multi_source,
          meta: "保留不同版本与清晰度",
          icon: Layers3,
          tone: "coral",
        }
      : {
          label: "待处理",
          value: stats?.media?.pending,
          meta: `其中 ${formatNumber(stats?.media?.review)} 条待确认`,
          icon: CircleHelp,
          tone: "amber",
        },
  ];

  return (
    <section className="stats-bar" aria-label="资料库统计">
      {values.map((item) => {
        const Icon = item.icon;
        return (
          <div className="metric" key={item.label}>
            <span className={`metric-icon ${item.tone}`}><Icon size={20} /></span>
            <div>
              <span>{item.label}</span>
              <strong>{formatNumber(item.value)}</strong>
              <small>{item.meta}</small>
            </div>
          </div>
        );
      })}
    </section>
  );
}
