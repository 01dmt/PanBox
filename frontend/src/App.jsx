import { useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import {
  getConfig,
  getMedia,
  getMediaFilters,
  getMediaDetail,
  getResources,
  getResourceFilters,
  getResourceDetail,
  updateResource,
  checkResource,
  getStats,
  linkCandidate,
  rejectCandidate,
  searchCandidates,
  syncPending,
  unlinkTmdb,
  updateMedia,
} from "./api";
import Sidebar from "./components/Sidebar";
import Topbar from "./components/Topbar";
import StatsBar from "./components/StatsBar";
import Dashboard from "./components/Dashboard";
import FilterToolbar from "./components/FilterToolbar";
import MediaTable from "./components/MediaTable";
import Inspector from "./components/Inspector";
import ImportView from "./components/ImportView";
import SettingsView from "./components/SettingsView";
import MobileNav from "./components/MobileNav";
import ShareAuditStatus from "./components/ShareAuditStatus";
import LibraryHero from "./components/LibraryHero";
import CinematicFilters from "./components/CinematicFilters";
import CinematicPosterGrid from "./components/CinematicPosterGrid";
import ResourceLibrary, { ResourceDetail } from "./components/ResourceLibrary";
import ResourceFilterToolbar from "./components/ResourceFilterToolbar";


const DATA_VIEWS = new Set(["library", "resources", "pending", "duplicates"]);

const DEFAULT_FILTERS = {
  status: "all",
  type: "all",
  resource_kind: "all",
  source: "all",
  availability: "available",
  year: "all",
  genre: "all",
  country: "all",
  quality: "all",
  codec: "all",
  hdr: "all",
  sort: "updated_desc",
  page: 1,
  pageSize: 30,
};

const DEFAULT_RESOURCE_FILTERS = {
  group: "all",
  resource_class: "all",
  provider: "all",
  availability: "all",
  tmdb_status: "all",
  year: "all",
  tag: "all",
  quality: "all",
  channel: "all",
  updated: "all",
  sort: "updated_desc",
  page: 1,
  pageSize: 30,
};


export default function App() {
  const [view, setView] = useState("library");
  const [filters, setFilters] = useState(DEFAULT_FILTERS);
  const [resourceFilters, setResourceFilters] = useState(DEFAULT_RESOURCE_FILTERS);
  const [search, setSearch] = useState("");
  const deferredSearch = useDeferredValue(search);
  const [stats, setStats] = useState(null);
  const [config, setConfig] = useState(null);
  const [filterOptions, setFilterOptions] = useState(null);
  const [resourceFilterOptions, setResourceFilterOptions] = useState(null);
  const [pageData, setPageData] = useState({ items: [], total: 0, page: 1, pages: 1 });
  const [resourcePageData, setResourcePageData] = useState({ items: [], total: 0, page: 1, pages: 1, summary: {} });
  const [selectedId, setSelectedId] = useState(null);
  const [selectedItem, setSelectedItem] = useState(null);
  const [selectedResourceId, setSelectedResourceId] = useState(null);
  const [selectedResource, setSelectedResource] = useState(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [resourceDetailLoading, setResourceDetailLoading] = useState(false);
  const [checkingResourceId, setCheckingResourceId] = useState(null);
  const [syncing, setSyncing] = useState(false);
  const [mobileInspectorOpen, setMobileInspectorOpen] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [manualImportOpen, setManualImportOpen] = useState(false);
  const searchRef = useRef(null);
  const mediaRequestRef = useRef(0);
  const mediaAbortRef = useRef(null);
  const lastDataViewRef = useRef("library");

  const effectiveParams = useMemo(() => {
    const status = view === "pending" && filters.status === "all" ? "pending" : filters.status;
    return {
      q: deferredSearch,
      status,
      type: filters.type,
      resource_kind: view === "library" ? "media" : view === "resources" ? "all" : "all",
      source: filters.source,
      availability: filters.availability,
      year: filters.year,
      genre: filters.genre,
      country: filters.country,
      quality: filters.quality,
      codec: filters.codec,
      hdr: filters.hdr,
      sort: filters.sort,
      page: filters.page,
      page_size: filters.pageSize,
      multi_source: view === "duplicates",
    };
  }, [deferredSearch, filters, view]);

  const loadSummary = useCallback(async () => {
    const [nextStats, nextConfig, nextOptions, nextResourceOptions] = await Promise.all([getStats(), getConfig(), getMediaFilters(), getResourceFilters()]);
    setStats(nextStats);
    setConfig(nextConfig);
    setFilterOptions(nextOptions);
    setResourceFilterOptions(nextResourceOptions);
  }, []);

  const loadMedia = useCallback(async () => {
    if (!DATA_VIEWS.has(view) || view === "resources") return;
    const requestId = ++mediaRequestRef.current;
    mediaAbortRef.current?.abort();
    const controller = new AbortController();
    mediaAbortRef.current = controller;
    setLoading(true);
    try {
      const result = await getMedia(effectiveParams, controller.signal);
      if (requestId !== mediaRequestRef.current) return;
      if (result.page > result.pages) {
        setFilters((current) => ({ ...current, page: result.pages }));
        return;
      }
      setPageData(result);
      setSelectedId((current) => {
        if (result.items.some((item) => item.id === current)) return current;
        return result.items[0]?.id ?? null;
      });
      setError("");
    } catch (nextError) {
      if (requestId === mediaRequestRef.current && nextError.name !== "AbortError") setError(nextError.message);
    } finally {
      if (requestId === mediaRequestRef.current) setLoading(false);
    }
  }, [effectiveParams, view]);

  const resourceParams = useMemo(() => ({
    q: deferredSearch,
    resource_group: resourceFilters.group,
    resource_class: resourceFilters.resource_class,
    provider: resourceFilters.provider,
    availability: resourceFilters.availability,
    tmdb_status: resourceFilters.tmdb_status,
    year: resourceFilters.year,
    tag: resourceFilters.tag === "all" ? "" : resourceFilters.tag,
    quality: resourceFilters.quality,
    channel: resourceFilters.channel,
    updated: resourceFilters.updated,
    sort: resourceFilters.sort,
    page: resourceFilters.page,
    page_size: resourceFilters.pageSize,
    aggregate: resourceFilters.group === "person" ? "person" : "",
  }), [deferredSearch, resourceFilters]);

  const loadResources = useCallback(async () => {
    if (view !== "resources") return;
    const requestId = ++mediaRequestRef.current;
    mediaAbortRef.current?.abort();
    const controller = new AbortController();
    mediaAbortRef.current = controller;
    setLoading(true);
    try {
      const result = await getResources(resourceParams, controller.signal);
      if (requestId !== mediaRequestRef.current) return;
      if (result.page > result.pages) {
        setResourceFilters((current) => ({ ...current, page: result.pages }));
        return;
      }
      setResourcePageData(result);
      setSelectedResourceId((current) => {
        const contains = result.items.some((item) => item.resources
          ? item.resources.some((resource) => resource.id === current)
          : item.id === current);
        if (contains) return current;
        return result.items[0]?.resources?.[0]?.id ?? result.items[0]?.id ?? null;
      });
      setError("");
    } catch (nextError) {
      if (requestId === mediaRequestRef.current && nextError.name !== "AbortError") setError(nextError.message);
    } finally {
      if (requestId === mediaRequestRef.current) setLoading(false);
    }
  }, [resourceParams, view]);

  useEffect(() => {
    loadSummary().catch((nextError) => setError(nextError.message));
  }, [loadSummary]);

  useEffect(() => {
    loadMedia().catch((nextError) => setError(nextError.message));
    return () => { mediaRequestRef.current += 1; mediaAbortRef.current?.abort(); };
  }, [loadMedia]);

  useEffect(() => {
    loadResources().catch((nextError) => setError(nextError.message));
    return () => { mediaRequestRef.current += 1; mediaAbortRef.current?.abort(); };
  }, [loadResources]);

  useEffect(() => {
    if (!selectedId || !DATA_VIEWS.has(view) || view === "resources") {
      setSelectedItem(null);
      return undefined;
    }
    let active = true;
    setSelectedItem(null);
    setDetailLoading(true);
    getMediaDetail(selectedId)
      .then((item) => {
        if (active) setSelectedItem(item);
      })
      .catch((nextError) => {
        if (active) setError(nextError.message);
      })
      .finally(() => {
        if (active) setDetailLoading(false);
      });
    return () => {
      active = false;
    };
  }, [selectedId, view]);

  useEffect(() => {
    if (!selectedResourceId || view !== "resources") {
      setSelectedResource(null);
      return undefined;
    }
    let active = true;
    setSelectedResource(null);
    setResourceDetailLoading(true);
    getResourceDetail(selectedResourceId)
      .then((item) => { if (active) setSelectedResource(item); })
      .catch((nextError) => { if (active) setError(nextError.message); })
      .finally(() => { if (active) setResourceDetailLoading(false); });
    return () => { active = false; };
  }, [selectedResourceId, view]);

  useEffect(() => {
    const onKeyDown = (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        searchRef.current?.focus();
      }
      if (event.key === "Escape") setMobileInspectorOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const changeView = useCallback((nextView) => {
    setView(nextView);
    setManualImportOpen(false);
    if (DATA_VIEWS.has(nextView) && nextView !== lastDataViewRef.current) {
      lastDataViewRef.current = nextView;
      setFilters((current) => ({ ...DEFAULT_FILTERS, availability: nextView === "library" ? "available" : "all", pageSize: current.pageSize }));
      setResourceFilters((current) => ({ ...DEFAULT_RESOURCE_FILTERS, pageSize: current.pageSize }));
      setSearch("");
    } else {
      setFilters((current) => ({ ...current, page: 1 }));
    }
    setMobileInspectorOpen(false);
    setNotice("");
    setError("");
  }, []);

  const changeFilters = useCallback((patch) => {
    setFilters((current) => ({ ...current, ...patch, page: patch.page ?? 1 }));
    setMobileInspectorOpen(false);
  }, []);

  const changeResourceFilters = useCallback((patch) => {
    setResourceFilters((current) => ({ ...current, ...patch, page: patch.page ?? 1 }));
    setMobileInspectorOpen(false);
  }, []);

  const clearFilters = useCallback(() => {
    setFilters((current) => ({ ...DEFAULT_FILTERS, availability: "all", pageSize: current.pageSize }));
    setSearch("");
    setMobileInspectorOpen(false);
  }, []);

  const clearResourceFilters = useCallback(() => {
    setResourceFilters((current) => ({ ...DEFAULT_RESOURCE_FILTERS, group: current.group, pageSize: current.pageSize }));
    setSearch("");
    setMobileInspectorOpen(false);
  }, []);

  const refreshAfterChange = useCallback(
    async (item) => {
      if (item?.id) {
        setSelectedId(item.id);
        setSelectedItem(item);
      }
      await Promise.all([loadSummary(), loadMedia(), loadResources()]);
    },
    [loadMedia, loadResources, loadSummary],
  );

  const handleSearchCandidates = useCallback(async () => {
    if (!selectedId) return;
    try {
      setDetailLoading(true);
      const result = await searchCandidates(selectedId);
      setSelectedItem(result.item);
      if (result.status === "removed") {
        setMobileInspectorOpen(false);
        setNotice(`已清理 ${result.removed_sources || 0} 条已取消分享，并移除无来源记录。`);
      } else {
        setNotice(result.removed_sources
          ? `已清理 ${result.removed_sources} 条已取消分享，记录已更新。`
          : result.status === "not_found" ? "TMDB 没有返回候选结果。" : "候选结果已更新。");
      }
      setError("");
      await refreshAfterChange(result.item);
    } catch (nextError) {
      setError(nextError.message);
    } finally {
      setDetailLoading(false);
    }
  }, [refreshAfterChange, selectedId]);

  const handleAuditedSources = useCallback(async () => {
    let item;
    if (selectedId) {
      try { item = await getMediaDetail(selectedId); } catch { /* The audit may have removed this item. */ }
    }
    try {
      await refreshAfterChange(item);
    } catch (nextError) {
      setError(nextError.message);
    }
  }, [refreshAfterChange, selectedId]);

  const handleLink = useCallback(
    async (candidate) => {
      if (!selectedId) return;
      try {
        setDetailLoading(true);
        const item = await linkCandidate(selectedId, candidate);
        setNotice(`已关联到 TMDB #${candidate.tmdb_id}。`);
        setError("");
        await refreshAfterChange(item);
      } catch (nextError) {
        setError(nextError.message);
      } finally {
        setDetailLoading(false);
      }
    },
    [refreshAfterChange, selectedId],
  );

  const handleReject = useCallback(
    async (candidateId) => {
      if (!selectedId) return;
      try {
        const item = await rejectCandidate(selectedId, candidateId);
        setSelectedItem(item);
      } catch (nextError) {
        setError(nextError.message);
      }
    },
    [selectedId],
  );

  const handleUnlink = useCallback(async () => {
    if (!selectedId) return;
    try {
      setDetailLoading(true);
      const item = await unlinkTmdb(selectedId);
      setNotice("已解除 TMDB 关联。 ");
      await refreshAfterChange(item);
    } catch (nextError) {
      setError(nextError.message);
    } finally {
      setDetailLoading(false);
    }
  }, [refreshAfterChange, selectedId]);

  const handleUpdate = useCallback(
    async (values) => {
      if (!selectedId) return;
      try {
        const item = await updateMedia(selectedId, values);
        setNotice("本地元数据已更新。 ");
        await refreshAfterChange(item);
      } catch (nextError) {
        setError(nextError.message);
      }
    },
    [refreshAfterChange, selectedId],
  );

  const handleSync = useCallback(async () => {
    try {
      setSyncing(true);
      const result = await syncPending(20);
      setNotice(
        `已处理 ${result.processed} 条：自动匹配 ${result.matched}，待确认 ${result.review}，未找到 ${result.not_found}。`
        + (result.removed_sources ? `已清理 ${result.removed_sources} 条已取消分享、${result.removed || 0} 条无来源记录。` : ""),
      );
      setError("");
      await Promise.all([loadSummary(), loadMedia(), loadResources()]);
    } catch (nextError) {
      setError(nextError.message);
    } finally {
      setSyncing(false);
    }
  }, [loadMedia, loadResources, loadSummary]);

  const handleResourceCheck = useCallback(async (resourceId) => {
    try {
      setCheckingResourceId(resourceId);
      const item = await checkResource(resourceId);
      setSelectedResource(item);
      setNotice(item.availability_status === "available" ? "来源检查完成，分享可用。" : "来源检查完成，请查看当前状态。 ");
      await Promise.all([loadSummary(), loadResources()]);
    } catch (nextError) {
      setError(nextError.message);
    } finally {
      setCheckingResourceId(null);
    }
  }, [loadResources, loadSummary]);

  const handleResourceUpdate = useCallback(async (values) => {
    if (!selectedResourceId) return;
    try {
      const item = await updateResource(selectedResourceId, values);
      setSelectedResource(item);
      setNotice("资源信息已更新。 ");
      await Promise.all([loadSummary(), loadResources()]);
    } catch (nextError) {
      setError(nextError.message);
    }
  }, [loadResources, loadSummary, selectedResourceId]);

  const handleResourceAction = useCallback(async (resourceId, action) => {
    try {
      const item = selectedResource?.id === resourceId ? selectedResource : await getResourceDetail(resourceId);
      if (!item?.url) throw new Error("来源链接暂不可用。");
      if (action === "open") {
        window.open(item.url, "_blank", "noopener,noreferrer");
      } else {
        await navigator.clipboard.writeText(item.url);
        setNotice("分享链接已复制。 ");
      }
    } catch (nextError) {
      setError(nextError.message);
    }
  }, [selectedResource]);

  const selectItem = useCallback((mediaId) => {
    setSelectedId(mediaId);
    setMobileInspectorOpen(true);
  }, []);

  const selectResource = useCallback((resourceId) => {
    setSelectedResourceId(resourceId);
    setMobileInspectorOpen(true);
  }, []);

  const openResourceMedia = useCallback((mediaId) => {
    if (!mediaId) return;
    changeView("library");
    setSelectedId(mediaId);
    setMobileInspectorOpen(true);
  }, [changeView]);

  return (
    <div className="app-shell">
      <Sidebar view={view} onChange={changeView} stats={stats} config={config} providerOptions={resourceFilterOptions?.providers} />
      <div className={`app-main ${view === "resources" ? "app-main-resources" : ""}`}>
        {view !== "library" && <Topbar
          view={view}
          search={search}
          onSearch={(value) => {
            setSearch(value);
            setFilters((current) => (current.page === 1 ? current : { ...current, page: 1 }));
            setResourceFilters((current) => (current.page === 1 ? current : { ...current, page: 1 }));
          }}
          onSubmitSearch={() => {
            setSearch(search.trim());
            setFilters((current) => ({ ...current, page: 1 }));
            setResourceFilters((current) => ({ ...current, page: 1 }));
            setMobileInspectorOpen(false);
          }}
          searchRef={searchRef}
          syncing={syncing}
          onSync={handleSync}
          onManualImport={() => setManualImportOpen(true)}
        />}

        {view !== "dashboard" && view !== "library" && view !== "resources" ? <ShareAuditStatus onSourcesChanged={handleAuditedSources} /> : null}
        {error ? (
          <div className="message-banner error" role="alert">
            {error}
            <button type="button" onClick={() => setError("")} aria-label="关闭错误提示">×</button>
          </div>
        ) : null}
        {notice ? (
          <div className="message-banner notice" role="status">
            {notice}
            <button type="button" onClick={() => setNotice("")} aria-label="关闭提示">×</button>
          </div>
        ) : null}

        {view === "dashboard" ? <Dashboard stats={stats} onPending={() => changeView("pending")} /> : null}

        {view === "library" ? (
          <main className="cinematic-library">
            <LibraryHero item={pageData.items.find((item) => item.backdrop_path) || pageData.items[0]} onOpen={(id) => { selectItem(id); setMobileInspectorOpen(true); }} />
            <CinematicFilters filters={filters} options={filterOptions} total={pageData.total} onChange={changeFilters} onReset={clearFilters} onMore={() => changeFilters({})} />
            <CinematicPosterGrid items={pageData.items} loading={loading} selectedId={selectedId} onSelect={(id) => { selectItem(id); setMobileInspectorOpen(true); }} page={pageData.page} pages={pageData.pages} total={pageData.total} pageSize={filters.pageSize} onPageChange={(page) => changeFilters({ page })} onPageSizeChange={(pageSize) => changeFilters({ pageSize })} onClearFilters={clearFilters} />
            {mobileInspectorOpen && <div className="cinema-drawer-backdrop" onClick={() => setMobileInspectorOpen(false)} />}
            <div className={`cinema-drawer ${mobileInspectorOpen ? "open" : ""}`}><Inspector readOnly item={selectedItem} sourceIds={pageData.items.find((item) => item.id === selectedId)?.matched_source_ids} loading={detailLoading} tmdbConfigured={Boolean(config?.tmdb_configured)} mobileOpen={mobileInspectorOpen} onClose={() => setMobileInspectorOpen(false)} onSearch={handleSearchCandidates} onLink={handleLink} onReject={handleReject} onUnlink={handleUnlink} onUpdate={handleUpdate} /></div>
          </main>
        ) : DATA_VIEWS.has(view) ? (
          <>
            {view !== "resources" ? <StatsBar stats={stats} activeView={view} providerOptions={resourceFilterOptions?.providers} /> : null}
            {view === "resources" ? <><ResourceFilterToolbar filters={resourceFilters} options={resourceFilterOptions} loading={loading} total={resourcePageData.total} summary={resourcePageData.summary} onChange={changeResourceFilters} onReset={clearResourceFilters} /><div className="resource-library-layout" id="resource-category-panel" role="tabpanel" aria-labelledby={`resource-tab-${resourceFilters.group}`}><ResourceLibrary items={resourcePageData.items} loading={loading} grouped={resourcePageData.aggregated === "person"} selectedId={selectedResourceId} onSelect={selectResource} total={resourcePageData.total} page={resourcePageData.page} pages={resourcePageData.pages} onPageChange={(page) => changeResourceFilters({ page })} onAction={handleResourceAction} onCheck={handleResourceCheck} checkingId={checkingResourceId} summary={resourcePageData.summary} onClearFilters={clearResourceFilters} /><ResourceDetail item={selectedResource} loading={resourceDetailLoading} onClose={() => setSelectedResourceId(null)} onAction={(resourceId, action) => action === "select" ? selectResource(resourceId) : handleResourceAction(resourceId, action)} onCheck={handleResourceCheck} checking={checkingResourceId === selectedResourceId} onUpdate={handleResourceUpdate} onViewMedia={openResourceMedia} mobileOpen={mobileInspectorOpen} /></div></> : null}
            <div className={`workspace-grid ${view === "resources" ? "resource-workspace-hidden" : ""}`}>
              <main className={`library-panel ${view === "resources" ? "resource-library-hidden" : ""}`}>
                <FilterToolbar
                  view={view}
                  filters={filters}
                  options={filterOptions}
                  search={search}
                  loading={loading}
                  total={pageData.total}
                  onChange={changeFilters}
                  onReset={clearFilters}
                  onClearSearch={() => { setSearch(""); changeFilters({}); }}
                  onRefresh={() => refreshAfterChange().catch((nextError) => setError(nextError.message))}
                />
                <MediaTable
                  items={pageData.items}
                  loading={loading}
                  selectedId={selectedId}
                  onSelect={selectItem}
                  page={pageData.page}
                  pages={pageData.pages}
                  total={pageData.total}
                  pageSize={filters.pageSize}
                  scrollKey={effectiveParams}
                  onPageChange={(page) => changeFilters({ page })}
                  onPageSizeChange={(pageSize) => changeFilters({ pageSize })}
                  onClearFilters={clearFilters}
                />
              </main>
              <Inspector
                item={selectedItem}
                sourceIds={pageData.items.find((item) => item.id === selectedId)?.matched_source_ids}
                loading={detailLoading}
                tmdbConfigured={Boolean(config?.tmdb_configured)}
                mobileOpen={mobileInspectorOpen}
                onClose={() => setMobileInspectorOpen(false)}
                onSearch={handleSearchCandidates}
                onLink={handleLink}
                onReject={handleReject}
                onUnlink={handleUnlink}
                onUpdate={handleUpdate}
              />
            </div>
          </>
        ) : null}

        {view === "imports" ? (
          <ImportView
            manualOpen={manualImportOpen}
            onCloseManual={() => setManualImportOpen(false)}
            onImported={async (result) => {
              setNotice(`导入完成：新增 ${result.inserted} 条，跳过重复 ${result.duplicates} 条。`);
              await loadSummary();
            }}
          />
        ) : null}

        {view === "settings" ? <SettingsView config={config} stats={stats} /> : null}
      </div>
      <MobileNav view={view} onChange={changeView} pending={stats?.media?.pending ?? 0} config={config} />
    </div>
  );
}
