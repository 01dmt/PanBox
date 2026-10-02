import { FolderInput, LayoutDashboard, Library, Settings } from "lucide-react";


const ITEMS = [
  { id: "dashboard", label: "仪表盘", icon: LayoutDashboard },
  { id: "library", label: "影视库", icon: Library },
  { id: "resources", label: "资源库", icon: FolderInput },
  { id: "imports", label: "入库", icon: FolderInput },
  { id: "settings", label: "设置", icon: Settings },
];


export default function MobileNav({ view, onChange, pending, config }) {
  return (
    <nav className="mobile-nav" aria-label="移动端导航">
      <div className={`mobile-tmdb-state ${config?.tmdb_configured ? "connected" : "offline"}`}>
        <i aria-hidden="true" />{config?.tmdb_configured ? "TMDB 已连接" : "TMDB 未配置"}
      </div>
      {ITEMS.map((item) => {
        const Icon = item.icon;
        return (
          <button type="button" key={item.id} className={view === item.id ? "active" : ""} onClick={() => onChange(item.id)}>
            <span><Icon size={20} /></span>
            {item.label}
          </button>
        );
      })}
    </nav>
  );
}
