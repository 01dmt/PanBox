import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import {
  getConfig,
  getMedia,
  getMediaFilters,
  getMediaDetail,
  getResources,
  getResourceGroup,
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
import ShellHeader from "./components/ShellHeader";
import Topbar from "./components/Topbar";
import StatsBar from "./components/StatsBar";
import Dashboard from "./components/Dashboard";
import FilterToolbar from "./components/FilterToolbar";
import MediaTable from "./components/MediaTable";
import PendingView from "./components/PendingView";
import Inspector from "./components/Inspector";
import ImportView from "./components/ImportView";
import SettingsView from "./components/SettingsView";
import MobileNav from "./components/MobileNav";
import Re0HeroCarousel from "./components/Re0HeroCarousel";
import Re0CatalogBrowser from "./components/Re0CatalogBrowser";
import Re0PosterGrid from "./components/Re0PosterGrid";
import Re0MovieDetail from "./components/Re0MovieDetail";
import ResourceLibrary, { PersonResources, ResourceDetail } from "./components/ResourceLibrary";
import ResourceFilterToolbar from "./components/ResourceFilterToolbar";
import {
  RESOURCE_DEFAULTS,
  defaultMediaFilters,
  isCatalogView,
  readMediaFilters,
  readResourceFilters,
  readSelectionId,
  withMediaFilters,
  withResourceFilters,
} from "./urlState";


const DATA_VIEWS = new Set(["library", "movies", "tv", "resources", "pending", "duplicates"]);

const VIEW_PATHS = Object.freeze({
  movies: "/movies",
  tv: "/tv",
  library: "/library",
  resources: "/resources",
  pending: "/pending",
  duplicates: "/duplicates",
  imports: "/imports",
  dashboard: "/dashboard",
  settings: "/settings",
});

const PATH_VIEWS = Object.freeze(
  Object.fromEntries(Object.entries(VIEW_PATHS).map(([view, path]) => [path, view])),
);

const DEFAULT_VIEW = "movies";
const IMPORT_STATUS_VALUES = new Set(["all", "imported", "failed", "duplicate", "ignored"]);


export default function App() {
  const location = useLocation();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const setSearchParamsRef = useRef(setSearchParams);
  useEffect(() => {
    setSearchParamsRef.current = setSearchParams;
  }, [setSearchParams]);
  const view = PATH_VIEWS[location.pathname] ?? DEFAULT_VIEW;
  const mediaFilterKey = useMemo(() => {
    const params = new URLSearchParams(searchParams);
    ["q", "media", "resource", "group_id"].forEach((key) => params.delete(key));
    return params.toString();
  }, [searchParams]);
  const resourceFilterKey = useMemo(() => {
    const params = new URLSearchParams(searchParams);
    ["q", "media", "resource", "group_id"].forEach((key) => params.delete(key));
    return params.toString();
  }, [searchParams]);
  const filters = useMemo(() => readMediaFilters(new URLSearchParams(mediaFilterKey), view), [mediaFilterKey, view]);
  const resourceFilters = useMemo(() => readResourceFilters(new URLSearchParams(resourceFilterKey)), [resourceFilterKey]);
  const search = searchParams.get("q") ?? "";
  const [debouncedSearch, setDebouncedSearch] = useState(search);
  useEffect(() => {
    if (!search) {
      setDebouncedSearch("");
      return undefined;
    }
    const timer = window.setTimeout(() => setDebouncedSearch(search), 250);
    return () => window.clearTimeout(timer);
  }, [search]);
  const mediaParam = readSelectionId(searchParams, "media");
  const resourceParam = readSelectionId(searchParams, "resource");
  const selectedGroupId = searchParams.get("group_id") || null;
  const [groupDetail, setGroupDetail] = useState(null);
  const [groupDetailLoading, setGroupDetailLoading] = useState(false);
  const [autoSelectedId, setAutoSelectedId] = useState(null);
  const [autoSelectedResourceId, setAutoSelectedResourceId] = useState(null);
  const selectedId = mediaParam ?? autoSelectedId;
  const selectedMediaDetailId = isCatalogView(view) ? mediaParam : null;
  const selectedResourceId = resourceParam ?? autoSelectedResourceId;
  const [theme, setTheme] = useState(() => {
    try {
      const saved = localStorage.getItem("panbox-theme");
      if (saved === "light" || saved === "dark") return saved;
    } catch {}
    return "dark";
  });
  const [stats, setStats] = useState(null);
  const [config, setConfig] = useState(null);
  const [filterOptions, setFilterOptions] = useState(null);
  const [resourceFilterOptions, setResourceFilterOptions] = useState(null);
  const [pageData, setPageData] = useState({ items: [], total: 0, page: 1, pages: 1 });
  const [resourcePageData, setResourcePageData] = useState({ items: [], total: 0, page: 1, pages: 1, summary: {} });
  const [selectedItem, setSelectedItem] = useState(null);
  const [selectedResource, setSelectedResource] = useState(null);
  const [resourceLayoutMode, setResourceLayoutMode] = useState(() => {
    try {
      return localStorage.getItem("panbox-resource-layout-mode") || "table";
    } catch {
      return "table";
    }
  });
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [resourceDetailLoading, setResourceDetailLoading] = useState(false);
  const [checkingResourceId, setCheckingResourceId] = useState(null);
  const [syncing, setSyncing] = useState(false);
  const [mobileInspectorOpen, setMobileInspectorOpen] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [manualImportOpen, setManualImportOpen] = useState(false);
  const importStatus = IMPORT_STATUS_VALUES.has(searchParams.get("status") || "all")
    ? (searchParams.get("status") || "all")
    : "all";
  const searchRef = useRef(null);
  const mediaRequestRef = useRef(0);
  const mediaAbortRef = useRef(null);
  const viewRef = useRef(view);

  const replaceParams = useCallback((build, { replace = true } = {}) => {
    setSearchParamsRef.current((current) => build(new URLSearchParams(current)), { replace });
  }, []);

  const changeImportStatus = useCallback((status) => {
    replaceParams((current) => {
      if (status && status !== "all") current.set("status", status);
      else current.delete("status");
      return current;
    });
  }, [replaceParams]);

  useEffect(() => {
    // 深链接里带 resource 时直接打开来源详情；query 移除后同步关闭弹窗。
    setMobileInspectorOpen(Boolean(resourceParam));
  }, [resourceParam]);

  const effectiveParams = useMemo(() => {
    const isCatalog = ["library", "movies", "tv"].includes(view);
    const status = view === "pending" && filters.status === "all" ? "pending"
      : isCatalog && filters.status === "all" ? "matched"
      : filters.status;
    const type = view === "movies" ? "movie"
      : view === "tv" ? "tv"
      : filters.type;

    return {
      q: debouncedSearch,
      status,
      type,
      category: view === "tv" ? (filters.category || "all") : "all",
      resource_kind: isCatalog ? "media" : view === "resources" ? "all" : "all",
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
  }, [debouncedSearch, filters, view]);

  const loadSummary = useCallback(async () => {
    const [nextStats, nextConfig, nextOptions] = await Promise.all([getStats(), getConfig(), getMediaFilters()]);
    setStats(nextStats);
    setConfig(nextConfig);
    setFilterOptions(nextOptions);
  }, []);

  const loadResourceOptions = useCallback(async () => {
    const nextOptions = await getResourceFilters();
    setResourceFilterOptions(nextOptions);
  }, []);

  useEffect(() => {
    if (view !== "resources" || resourceFilterOptions) return;
    loadResourceOptions().catch((nextError) => setError(nextError.message));
  }, [loadResourceOptions, resourceFilterOptions, view]);

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
        replaceParams((current) => withMediaFilters(current, { ...filters, page: result.pages }, view));
        return;
      }
      setPageData(result);
      setAutoSelectedId((current) => {
        if (result.items.some((item) => item.id === current)) return current;
        return result.items[0]?.id ?? null;
      });
      setError("");
    } catch (nextError) {
      if (requestId === mediaRequestRef.current && nextError.name !== "AbortError") setError(nextError.message);
    } finally {
      if (requestId === mediaRequestRef.current) setLoading(false);
    }
  }, [effectiveParams, filters, replaceParams, view]);

  const resourceParams = useMemo(() => ({
    q: debouncedSearch,
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
    aggregate: ["person", "series"].includes(resourceFilters.group) ? resourceFilters.group : "",
    include_resources: false,
  }), [debouncedSearch, resourceFilters]);

  const activeGroup = useMemo(() => {
    if (!selectedGroupId) return null;
    if (groupDetail?.id === selectedGroupId) return groupDetail;
    return resourcePageData.items.find((item) => item.id === selectedGroupId) ?? null;
  }, [groupDetail, resourcePageData, selectedGroupId]);




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
        replaceParams((current) => withResourceFilters(current, { ...resourceFilters, page: result.pages }));
        return;
      }
      setResourcePageData(result);
      setAutoSelectedResourceId((current) => {
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
  }, [replaceParams, resourceFilters, resourceParams, view]);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    try {
      localStorage.setItem("panbox-theme", theme);
    } catch {}
  }, [theme]);

  const toggleTheme = useCallback(() => {
    setTheme((current) => (current === "dark" ? "light" : "dark"));
  }, []);

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
    if (!selectedGroupId) {
      setGroupDetail(null);
      setGroupDetailLoading(false);
      return undefined;
    }
    if (groupDetail?.id === selectedGroupId) return undefined;
    const controller = new AbortController();
    let active = true;
    setGroupDetailLoading(true);
    getResourceGroup(selectedGroupId, resourceParams, controller.signal)
      .then((item) => {
        if (!active) return;
        setGroupDetail(item);
        setAutoSelectedResourceId(item.resources?.[0]?.id ?? null);
        setError("");
      })
      .catch((nextError) => {
        if (active && nextError.name !== "AbortError") setError(nextError.message);
      })
      .finally(() => {
        if (active) setGroupDetailLoading(false);
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [groupDetail, resourceParams, selectedGroupId]);

  useEffect(() => {
    if (!selectedId || !DATA_VIEWS.has(view) || view === "resources" || isCatalogView(view)) {
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
    if (!resourceParam || view !== "resources") {
      setSelectedResource(null);
      return undefined;
    }
    let active = true;
    setSelectedResource(null);
    setResourceDetailLoading(true);
    getResourceDetail(resourceParam)
      .then((item) => { if (active) setSelectedResource(item); })
      .catch((nextError) => { if (active) setError(nextError.message); })
      .finally(() => { if (active) setResourceDetailLoading(false); });
    return () => { active = false; };
  }, [resourceParam, view]);

  const closeDetails = useCallback(() => {
    setMobileInspectorOpen(false);
    setAutoSelectedResourceId(null);
    replaceParams((current) => {
      current.delete("media");
      current.delete("resource");
      return current;
    });
  }, [replaceParams]);

  useEffect(() => {
    const onKeyDown = (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        searchRef.current?.focus();
      }
      if (event.key === "Escape") closeDetails();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [closeDetails]);

  const applyView = useCallback((nextView) => {
    const previousView = viewRef.current;
    viewRef.current = nextView;
    if (previousView !== nextView) {
      setAutoSelectedId(null);
      setAutoSelectedResourceId(null);
      setSelectedItem(null);
      setSelectedResource(null);
    }
    setManualImportOpen(false);
    setMobileInspectorOpen(false);
    setNotice("");
    setError("");
  }, []);

  // URL 是页面视图的唯一来源；浏览器前进/后退和直接打开深链接都走这里。
  useEffect(() => {
    const routeView = PATH_VIEWS[location.pathname];
    if (!routeView) {
      navigate(VIEW_PATHS[DEFAULT_VIEW], { replace: true });
      if (viewRef.current !== DEFAULT_VIEW) applyView(DEFAULT_VIEW);
      return;
    }
    if (viewRef.current !== routeView) applyView(routeView);
  }, [location.pathname, navigate, applyView]);

  const changeView = useCallback((nextView) => {
    const path = VIEW_PATHS[nextView];
    if (!path) return;
    applyView(nextView);
    if (viewRef.current === nextView && PATH_VIEWS[location.pathname] === nextView) {
      // 重复点击当前导航项：保留筛选，回到第一页并关闭详情。
      replaceParams((current) => {
        current.delete("page");
        current.delete("media");
        current.delete("resource");
        current.delete("group_id");
        return current;
      }, { replace: true });
      return;
    }
    // 切换页面时清空 query，使用新页面的默认筛选。
    navigate(path);
  }, [applyView, location.pathname, navigate, replaceParams]);

  const changeFilters = useCallback((patch) => {
    const next = { ...filters, ...patch, page: patch.page ?? 1 };
    replaceParams((current) => withMediaFilters(current, next, view));
    setMobileInspectorOpen(false);
  }, [filters, replaceParams, view]);

  const handleSearch = useCallback((value) => {
    replaceParams((current) => {
      if (value) current.set("q", value);
      else current.delete("q");
      // 搜索条件变化后回到第一页，并退出不再匹配的详情。
      current.delete("page");
      current.delete("media");
      current.delete("resource");
      current.delete("group_id");
      return current;
    });
    setAutoSelectedId(null);
    setAutoSelectedResourceId(null);
  }, [replaceParams]);

  const changeResourceFilters = useCallback((patch) => {
    const next = { ...resourceFilters, ...patch, page: patch.page ?? 1 };
    replaceParams((current) => withResourceFilters(current, next));
    setAutoSelectedResourceId(null);
    setMobileInspectorOpen(false);
  }, [replaceParams, resourceFilters]);

  const clearFilters = useCallback(() => {
    const next = { ...defaultMediaFilters(view), availability: "all", pageSize: filters.pageSize };
    replaceParams((current) => {
      const nextParams = withMediaFilters(current, next, view);
      nextParams.delete("q");
      return nextParams;
    });
    setMobileInspectorOpen(false);
  }, [filters.pageSize, replaceParams, view]);

  const clearResourceFilters = useCallback(() => {
    const next = { ...RESOURCE_DEFAULTS, group: resourceFilters.group, pageSize: resourceFilters.pageSize };
    replaceParams((current) => {
      const nextParams = withResourceFilters(current, next);
      nextParams.delete("q");
      return nextParams;
    });
    setAutoSelectedResourceId(null);
    setMobileInspectorOpen(false);
  }, [replaceParams, resourceFilters.group, resourceFilters.pageSize]);

  const refreshAfterChange = useCallback(
    async (item) => {
      if (item?.id) {
        setAutoSelectedId(item.id);
        setSelectedItem(item);
      }
      setGroupDetail(null);
      const requests = [loadSummary(), loadMedia(), loadResources()];
      if (view === "resources") requests.push(loadResourceOptions());
      await Promise.all(requests);
    },
    [loadMedia, loadResourceOptions, loadResources, loadSummary, view],
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
      const requests = [loadSummary(), loadMedia(), loadResources()];
      if (view === "resources") requests.push(loadResourceOptions());
      await Promise.all(requests);
    } catch (nextError) {
      setError(nextError.message);
    } finally {
      setSyncing(false);
    }
  }, [loadMedia, loadResourceOptions, loadResources, loadSummary, view]);

  const handleResourceCheck = useCallback(async (resourceId) => {
    try {
      setCheckingResourceId(resourceId);
      const item = await checkResource(resourceId);
      setSelectedResource(item);
      setNotice(item.availability_status === "available" ? "来源检查完成，分享可用。" : "来源检查完成，请查看当前状态。 ");
      await Promise.all([loadSummary(), loadResources(), loadResourceOptions()]);
    } catch (nextError) {
      setError(nextError.message);
    } finally {
      setCheckingResourceId(null);
    }
  }, [loadResourceOptions, loadResources, loadSummary]);

  const handleResourceUpdate = useCallback(async (values) => {
    if (!selectedResourceId) return;
    try {
      const item = await updateResource(selectedResourceId, values);
      setSelectedResource(item);
      setNotice("资源信息已更新。 ");
      await Promise.all([loadSummary(), loadResources(), loadResourceOptions()]);
    } catch (nextError) {
      setError(nextError.message);
    }
  }, [loadResourceOptions, loadResources, loadSummary, selectedResourceId]);

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

  const closeMediaDetail = useCallback(() => {
    replaceParams((current) => {
      current.delete("media");
      return current;
    });
  }, [replaceParams]);

  const closeResourceDetail = useCallback(() => {
    setAutoSelectedResourceId(null);
    replaceParams((current) => {
      current.delete("resource");
      return current;
    });
  }, [replaceParams]);

  const openMediaDetail = useCallback((mediaId) => {
    replaceParams((current) => {
      current.set("media", String(mediaId));
      return current;
    }, { replace: false });
  }, [replaceParams]);

  const selectItem = useCallback((mediaId) => {
    replaceParams((current) => {
      current.set("media", String(mediaId));
      return current;
    }, { replace: false });
    setMobileInspectorOpen(true);
  }, [replaceParams]);

  const selectResource = useCallback((resourceId) => {
    replaceParams((current) => {
      current.set("resource", String(resourceId));
      return current;
    }, { replace: false });
    setMobileInspectorOpen(true);
  }, [replaceParams]);

  const openGroup = useCallback((groupId) => {
    setGroupDetail(null);
    setAutoSelectedResourceId(null);
    replaceParams((current) => {
      current.set("group_id", groupId);
      current.delete("resource");
      return current;
    }, { replace: false });
  }, [replaceParams]);

  const closeGroup = useCallback(() => {
    setGroupDetail(null);
    setGroupDetailLoading(false);
    setAutoSelectedResourceId(null);
    replaceParams((current) => {
      current.delete("group_id");
      current.delete("resource");
      return current;
    });
  }, [replaceParams]);

  const openResourceMedia = useCallback((mediaId) => {
    if (!mediaId) return;
    applyView("movies");
    const params = new URLSearchParams({ media: String(mediaId) });
    navigate(`${VIEW_PATHS.movies}?${params}`);
  }, [applyView, navigate]);

  return (
    <div className="app-shell" data-theme={theme}>
      <ShellHeader
        view={view}
        onChange={changeView}
        stats={stats}
        config={config}
        theme={theme}
        onToggleTheme={toggleTheme}
        syncing={syncing}
        onSync={handleSync}
        onManualImport={() => setManualImportOpen(true)}
      />
      <div className={`app-main ${view === "resources" ? "app-main-resources" : ["library", "movies", "tv"].includes(view) ? "app-main-library" : ""}`}>
        {!["library", "movies", "tv", "resources", "pending", "dashboard", "imports"].includes(view) && <Topbar
          view={view}
          search={search}
          onSearch={handleSearch}
          onSubmitSearch={() => {
            handleSearch(search.trim());
            setMobileInspectorOpen(false);
          }}
          searchRef={searchRef}
          syncing={syncing}
          onSync={handleSync}
          onManualImport={() => setManualImportOpen(true)}
        />}

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

        {view === "dashboard" ? (
          <Dashboard
            onPending={() => changeView("pending")}
            onReview={() => {
              applyView("pending");
              navigate(`${VIEW_PATHS.pending}?status=review`);
            }}
            onResources={() => changeView("resources")}
            onImports={(status) => {
              applyView("imports");
              const params = new URLSearchParams();
              if (status && status !== "all") params.set("status", status);
              const query = params.toString();
              navigate(`${VIEW_PATHS.imports}${query ? `?${query}` : ""}`);
            }}
          />
        ) : null}

        {view === "pending" ? (
          <PendingView
            stats={stats}
            filters={filters}
            options={filterOptions}
            search={search}
            searchRef={searchRef}
            pageData={pageData}
            loading={loading}
            selectedId={selectedId}
            selectedItem={selectedItem}
            detailLoading={detailLoading}
            tmdbConfigured={Boolean(config?.tmdb_configured)}
            mobileInspectorOpen={mobileInspectorOpen}
            syncing={syncing}
            onSearch={handleSearch}
            onChangeFilters={changeFilters}
            onClearSearch={() => handleSearch("")}
            onRefresh={() => refreshAfterChange().catch((nextError) => setError(nextError.message))}
            onSelect={selectItem}
            onCloseInspector={() => setMobileInspectorOpen(false)}
            onSync={handleSync}
            onSearchCandidates={handleSearchCandidates}
            onLink={handleLink}
            onReject={handleReject}
            onUnlink={handleUnlink}
            onUpdate={handleUpdate}
          />
        ) : ["library", "movies", "tv"].includes(view) ? (
          selectedMediaDetailId ? (
            <Re0MovieDetail
              mediaId={selectedMediaDetailId}
              onClose={closeMediaDetail}
              onLink={handleLink}
              onUnlink={handleUnlink}
              onSearchCandidates={handleSearchCandidates}
              tmdbConfigured={Boolean(config?.tmdb_configured)}
              onCheckResource={handleResourceCheck}
              onResourceAction={handleResourceAction}
            />
          ) : (
            <main className="cinematic-library">
              <Re0HeroCarousel items={pageData.items} mode={view === "tv" ? "tv" : "movie"} onOpen={openMediaDetail} />
              <Re0CatalogBrowser filters={filters} options={filterOptions} total={pageData.total} mode={view === "tv" ? "tv" : "movie"} search={search} onChange={changeFilters} onSearch={handleSearch} onReset={clearFilters} />
              <Re0PosterGrid items={pageData.items} loading={loading} selectedId={selectedId} mode={view === "tv" ? "tv" : "movie"} onSelect={openMediaDetail} page={pageData.page} pages={pageData.pages} total={pageData.total} pageSize={filters.pageSize} onPageChange={(page) => changeFilters({ page })} onPageSizeChange={(pageSize) => changeFilters({ pageSize })} onClearFilters={clearFilters} />
              {mobileInspectorOpen && <div className="cinema-drawer-backdrop" onClick={() => setMobileInspectorOpen(false)} />}
              <div className={`cinema-drawer ${mobileInspectorOpen ? "open" : ""}`}><Inspector readOnly item={selectedItem} sourceIds={pageData.items.find((item) => item.id === selectedId)?.matched_source_ids} loading={detailLoading} tmdbConfigured={Boolean(config?.tmdb_configured)} mobileOpen={mobileInspectorOpen} onClose={() => setMobileInspectorOpen(false)} onSearch={handleSearchCandidates} onLink={handleLink} onReject={handleReject} onUnlink={handleUnlink} onUpdate={handleUpdate} /></div>
            </main>
          )
        ) : DATA_VIEWS.has(view) ? (
          <>
            {view !== "resources" ? <StatsBar stats={stats} activeView={view} providerOptions={resourceFilterOptions?.providers} /> : null}
            {view === "resources" ? (
              <>
                <ResourceFilterToolbar
                  filters={resourceFilters}
                  options={resourceFilterOptions}
                  loading={loading}
                  total={resourcePageData.total}
                  summary={resourcePageData.summary}
                  search={search}
                  onSearch={handleSearch}
                  layoutMode={resourceLayoutMode}
                  onLayoutModeChange={(mode) => {
                    setResourceLayoutMode(mode);
                    try {
                      localStorage.setItem("panbox-resource-layout-mode", mode);
                    } catch {}
                  }}
                  onChange={changeResourceFilters}
                  onReset={clearResourceFilters}
                />
                <div
                  className="resource-library-layout"
                  id="resource-category-panel"
                  role="tabpanel"
                  aria-labelledby={`resource-tab-${resourceFilters.group}`}
                >
                  {activeGroup ? (
                    <PersonResources
                      group={activeGroup}
                      loading={groupDetailLoading}
                      onBack={closeGroup}
                      selectedId={selectedResourceId}
                      onSelect={selectResource}
                      onAction={handleResourceAction}
                      onCheck={handleResourceCheck}
                      checkingId={checkingResourceId}
                    />
                  ) : (
                    <ResourceLibrary
                      items={resourcePageData.items}
                      loading={loading}
                      grouped={resourcePageData.aggregated}
                      selectedId={selectedResourceId}
                      onSelect={selectResource}
                      total={resourcePageData.total}
                      page={resourcePageData.page}
                      pages={resourcePageData.pages}
                      pageSize={resourceFilters.pageSize}
                      onPageChange={(page) => changeResourceFilters({ page })}
                      onPageSizeChange={(pageSize) => changeResourceFilters({ pageSize })}
                      onAction={handleResourceAction}
                      onCheck={handleResourceCheck}
                      checkingId={checkingResourceId}
                      summary={resourcePageData.summary}
                      onClearFilters={clearResourceFilters}
                      onOpenGroup={openGroup}
                    />
                  )}
                  <ResourceDetail
                    item={selectedResource}
                    loading={resourceDetailLoading}
                    onClose={() => {
                      closeResourceDetail();
                      setMobileInspectorOpen(false);
                    }}
                    onAction={(resourceId, action) =>
                      action === "select"
                        ? selectResource(resourceId)
                        : handleResourceAction(resourceId, action)
                    }
                    onCheck={handleResourceCheck}
                    checking={checkingResourceId === selectedResourceId}
                    onUpdate={handleResourceUpdate}
                    onViewMedia={openResourceMedia}
                    open={Boolean(selectedResourceId && mobileInspectorOpen)}
                  />
                </div>
              </>
            ) : null}
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
                  onClearSearch={() => handleSearch("")}
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
            initialStatus={importStatus}
            status={importStatus}
            onStatusChange={changeImportStatus}
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
