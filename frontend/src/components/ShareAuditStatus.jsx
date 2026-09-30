import { useEffect, useRef, useState } from "react";
import { Pause } from "lucide-react";
import { getShareAudit, pauseShareAudit } from "../api";


const STATUS_LABELS = {
  running: "检查中", cooldown: "间歇休息", paused: "已暂停", interrupted: "已中断",
  paused_risk: "访问异常，保护性停止", paused_error: "请求异常，已停止", error: "已停止", complete: "已完成",
};

export default function ShareAuditStatus({ onSourcesChanged }) {
  const [audit, setAudit] = useState(null);
  const [pausing, setPausing] = useState(false);
  const [error, setError] = useState("");
  const removedRef = useRef(null);
  useEffect(() => {
    let active = true;
    let timer;
    const poll = async () => {
      try {
        const next = await getShareAudit();
        if (active) {
          setAudit(next);
          const previous = removedRef.current;
          removedRef.current = next.removed ?? 0;
          if (previous !== null && previous !== removedRef.current) onSourcesChanged();
        }
      } catch {
        // Keep the last known progress when a local status request fails.
      } finally {
        if (active) timer = window.setTimeout(poll, 15000);
      }
    };
    poll();
    return () => { active = false; window.clearTimeout(timer); };
  }, [onSourcesChanged]);

  if (!audit || audit.status === "idle") return null;
  const running = ["running", "cooldown"].includes(audit.status);
  const pause = async () => {
    setPausing(true);
    try {
      setAudit(await pauseShareAudit());
      setError("");
    } catch (nextError) {
      setError(nextError.message);
    } finally {
      setPausing(false);
    }
  };
  return (
    <div className="message-banner notice audit-banner" role="status">
      <span>
        链接检查：{audit.pause_requested && running ? "正在暂停" : STATUS_LABELS[audit.status] || audit.status}
        {audit.total != null ? ` · ${audit.processed || 0} / ${audit.total} · 已剔除 ${audit.removed || 0} 条` : ""}
        {error ? ` · ${error}` : ""}
      </span>
      {running ? <button type="button" className="icon-only" onClick={pause} disabled={pausing || audit.pause_requested} title="暂停链接检查" aria-label="暂停链接检查"><Pause size={16} aria-hidden="true" /></button> : null}
    </div>
  );
}
