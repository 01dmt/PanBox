import { useState } from "react";
import { RefreshCw, RotateCcw, SlidersHorizontal, X } from "lucide-react";
import { formatNumber } from "../lib/format";


const AVAILABILITY = [["all", "全部作品"], ["available", "有资源"], ["missing", "无资源"]];
const TYPES = [["all", "全部类型"], ["movie", "电影"], ["tv", "剧集"], ["unknown", "待判断"]];
const SOURCES = [["all", "全部来源"], ["115", "115 分享"], ["ed2k", "ED2K"]];
const STATUSES = [["all", "全部状态"], ["matched", "已匹配"], ["pending", "全部待处理"], ["unsearched", "未搜索"], ["review", "待确认"], ["not_found", "未找到"], ["error", "匹配错误"]];
const QUALITIES = [["all", "全部清晰度"], ["2160P", "4K / 2160P"], ["1080P", "1080P"], ["1080I", "1080I"], ["720P", "720P"], ["480P", "480P"], ["unknown", "未识别"]];
const CODECS = [["all", "全部编码"], ["h265", "H.265 / HEVC"], ["h264", "H.264 / AVC"], ["av1", "AV1"], ["unknown", "未识别"]];
const HDR = [["all", "全部动态范围"], ["dv", "杜比视界 / DV"], ["hdr", "HDR / HDR10+"], ["sdr", "SDR"], ["unknown", "未识别"]];
const FIELDS = { availability: "资源", type: "类型", source: "来源", status: "匹配状态", year: "年份", genre: "题材", country: "国家 / 地区", quality: "清晰度", codec: "编码", hdr: "动态范围" };
const ADVANCED = ["year", "genre", "country", "quality", "codec", "hdr"];
const regionNames = new Intl.DisplayNames(["zh-CN"], { type: "region", style: "short" });

function SelectFilter({ name, label, value, options, onChange, children }) {
  return <label className="filter-control"><span>{label}</span>
    <select aria-label={label} value={value} onChange={(event) => onChange({ [name]: event.target.value })}>
      {options.map(([id, text]) => <option key={id} value={id}>{text}</option>)}
      {children}
    </select>
  </label>;
}

export default function FilterToolbar({ view, filters, options, search, total, loading, onChange, onClearSearch, onReset, onRefresh }) {
  const [expanded, setExpanded] = useState(false);
  const years = (options?.years || []).map((year) => [String(year), `${year} 年`]);
  const decades = [...new Set((options?.years || []).map((year) => Math.floor(year / 10) * 10))].map((year) => [`${year}s`, `${year} - ${year + 9}`]);
  const genreOptions = [["all", "全部题材"], ...(options?.genres || []).map(({ id, name }) => [String(id), ({ 10765: "科幻与奇幻", 10768: "战争与政治" })[id] || name])];
  const countryOptions = [["all", "全部国家 / 地区"], ...(options?.countries || []).map((code) => [code, regionNames.of(code)])];
  const statusOptions = view === "pending" ? [["all", "全部待处理"], ...STATUSES.filter(([id]) => !["all", "matched", "pending"].includes(id))] : STATUSES;
  const choices = { availability: AVAILABILITY, type: TYPES, source: SOURCES, status: statusOptions,
    year: [["all", "全部年份"], ["unknown", "年份未知"], ...years, ...decades], genre: genreOptions,
    country: countryOptions, quality: QUALITIES, codec: CODECS, hdr: HDR };
  const active = Object.keys(FIELDS).filter((key) => filters[key] && filters[key] !== "all");
  const advancedCount = active.filter((key) => ADVANCED.includes(key)).length;
  const canReset = active.length > 0 || Boolean(search.trim());

  return (
    <section className="filter-toolbar" aria-label="资源筛选">
      <div className="filter-overview">
        <div className="filter-result-count" role="status" aria-live="polite"><strong>{loading ? "查询中" : `${formatNumber(total)} 部作品`}</strong><span>{view === "pending" ? "待处理" : view === "duplicates" ? "多来源" : "资源列表"}</span></div>
        <div className="filter-tools">
          <select value={filters.sort} onChange={(event) => onChange({ sort: event.target.value })} aria-label="排序方式">
            <option value="updated_desc">最近更新</option><option value="title_asc">标题排序</option>
            <option value="year_desc">年份从新到旧</option><option value="year_asc">年份从旧到新</option>
            <option value="sources_desc">符合来源最多</option><option value="confidence_asc">低置信度优先</option>
          </select>
          <button className={`button small filter-expand ${expanded ? "active" : ""}`} type="button" onClick={() => setExpanded((value) => !value)} aria-expanded={expanded} aria-controls="advanced-resource-filters">
            <SlidersHorizontal size={15} />更多筛选{advancedCount ? <b>{advancedCount}</b> : null}
          </button>
          <button type="button" className="icon-only" onClick={onReset} disabled={!canReset} title="清除全部筛选" aria-label="清除全部筛选"><RotateCcw size={16} /></button>
          <button type="button" className="icon-only" onClick={onRefresh} disabled={loading} title="刷新列表" aria-label="刷新列表"><RefreshCw size={16} className={loading ? "spin" : ""} /></button>
        </div>
      </div>
      <div className="filter-main">
        {["availability", "type", "source", "status"].map((key) => <SelectFilter key={key} name={key} label={FIELDS[key]} value={filters[key]} options={choices[key]} onChange={onChange} />)}
      </div>
      {expanded ? <div className="filter-advanced" id="advanced-resource-filters">
        <SelectFilter name="year" label="年份" value={filters.year} options={[["all", "全部年份"], ["unknown", "年份未知"]]} onChange={onChange}>
          <optgroup label="年代">{decades.map(([id, label]) => <option key={id} value={id}>{label}</option>)}</optgroup>
          <optgroup label="具体年份">{years.map(([id, label]) => <option key={id} value={id}>{label}</option>)}</optgroup>
        </SelectFilter>
        {ADVANCED.slice(1).map((key) => <SelectFilter key={key} name={key} label={FIELDS[key]} value={filters[key]} options={choices[key]} onChange={onChange} />)}
      </div> : null}
      {canReset ? <ul className="active-filters" aria-label="当前筛选条件">
        {search.trim() ? <li><span>搜索：{search.trim()}</span><button type="button" className="icon-only" onClick={onClearSearch} aria-label="移除搜索条件" title="移除搜索条件"><X size={12} /></button></li> : null}
        {active.map((key) => <li key={key}><span>{FIELDS[key]}：{choices[key].find(([value]) => value === filters[key])?.[1] || filters[key]}</span>
          <button type="button" className="icon-only" onClick={() => onChange({ [key]: "all" })} aria-label={`移除${FIELDS[key]}筛选`} title={`移除${FIELDS[key]}筛选`}><X size={12} /></button></li>)}
      </ul> : null}
    </section>
  );
}
