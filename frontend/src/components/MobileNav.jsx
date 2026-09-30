import { CircleHelp, FolderInput, LayoutDashboard, Library, Settings } from "lucide-react";
import { formatNumber } from "../lib/format";


const ITEMS = [
  { id: "dashboard", label: "仪表盘", icon: LayoutDashboard },
  { id: "library", label: "资料库", icon: Library },
  { id: "pending", label: "待匹配", icon: CircleHelp },
  { id: "imports", label: "导入", icon: FolderInput },
  { id: "settings", label: "设置", icon: Settings },
];


export default function MobileNav({ view, onChange, pending }) {
  return (
    <nav className="mobile-nav" aria-label="移动端导航">
      {ITEMS.map((item) => {
        const Icon = item.icon;
        return (
          <button type="button" key={item.id} className={view === item.id ? "active" : ""} onClick={() => onChange(item.id)}>
            <span><Icon size={20} />{item.id === "pending" && pending ? <small>{formatNumber(Math.min(pending, 999))}</small> : null}</span>
            {item.label}
          </button>
        );
      })}
    </nav>
  );
}

