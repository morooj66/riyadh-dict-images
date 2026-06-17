import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { api, type EntryDetail, type EntrySummary, type ImageSummary } from "../api/client";
import { WordDetailPanel } from "../components/WordDetailPanel";
import { WordSidebar } from "../components/WordSidebar";
import { HEADER_FILTERS } from "../utils/statusLabels";
import { prepareSidebarEntries } from "../utils/entryDedupe";

function toEntrySummary(detail: EntryDetail): EntrySummary {
  return {
    id: detail.id,
    word: detail.word,
    definition: detail.definition,
    category: detail.category,
    status: detail.status,
    prompt_family: detail.prompt_family,
    has_image: Boolean(detail.current_image_id) || detail.image_count > 0,
    image_count: detail.image_count,
    updated_at: detail.updated_at,
  };
}

const PAGE_SIZE = 40;

function queueStatus(entry: EntryDetail | null): string {
  if (!entry) return "needs_review";
  return entry.status === "needs_selection" ? "needs_selection" : "needs_review";
}

const WORD_ROUTE_RE = /^\/word\/([^/]+)/;

export function DictionaryPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const routeId = useMemo(() => {
    const match = WORD_ROUTE_RE.exec(location.pathname);
    return match?.[1];
  }, [location.pathname]);

  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [listItems, setListItems] = useState<EntrySummary[]>([]);
  const [listPage, setListPage] = useState(1);
  const [listTotalPages, setListTotalPages] = useState(1);
  const [listLoading, setListLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [loadingAll, setLoadingAll] = useState(false);
  const [listError, setListError] = useState<string | null>(null);
  const [totalRawEntries, setTotalRawEntries] = useState(0);
  const fetchedPageRef = useRef(0);

  const [selectedId, setSelectedId] = useState<string | null>(routeId ?? null);
  const [entry, setEntry] = useState<EntryDetail | null>(null);
  const [candidates, setCandidates] = useState<ImageSummary[]>([]);
  const [detailLoading, setDetailLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [regenerating, setRegenerating] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [rejectionReason, setRejectionReason] = useState("");
  const [reviewerVision, setReviewerVision] = useState("");
  const [notes, setNotes] = useState("");

  const patchEntryInList = useCallback((summary: EntrySummary) => {
    setListItems((prev) => {
      if (!prev.some((item) => item.id === summary.id)) return prev;
      return prepareSidebarEntries(
        prev.map((item) => (item.id === summary.id ? summary : item)),
      );
    });
  }, []);

  const loadList = useCallback(
    async (page: number, append: boolean) => {
      if (append) {
        setLoadingMore(true);
      } else {
        setListLoading(true);
      }
      setListError(null);
      try {
        const params = new URLSearchParams({
          page: String(page),
          page_size: String(PAGE_SIZE),
        });
        if (search.trim()) params.set("search", search.trim());
        if (statusFilter) params.set("status", statusFilter);

        const data = await api.entries(params);
        setTotalRawEntries(data.total);
        setListItems((prev) => {
          const merged = append
            ? (() => {
                const seen = new Set(prev.map((item) => item.id));
                const fresh = data.items.filter((item) => !seen.has(item.id));
                return [...prev, ...fresh];
              })()
            : data.items;
          return prepareSidebarEntries(merged);
        });
        fetchedPageRef.current = data.page;
        setListPage(data.page);
        setListTotalPages(data.total_pages);
      } catch (e) {
        setListError(e instanceof Error ? e.message : "تعذر تحميل القائمة");
        if (!append) setListItems([]);
      } finally {
        if (append) {
          setLoadingMore(false);
        } else {
          setListLoading(false);
        }
      }
    },
    [search, statusFilter],
  );

  const handleLoadMore = useCallback(() => {
    const nextPage = fetchedPageRef.current + 1;
    if (nextPage > listTotalPages || loadingMore || listLoading || loadingAll) return;
    void loadList(nextPage, true);
  }, [listTotalPages, loadingMore, listLoading, loadingAll, loadList]);

  const handleLoadAll = useCallback(async () => {
    if (loadingAll || loadingMore || listLoading) return;
    setLoadingAll(true);
    setListError(null);
    try {
      // Collect all items starting from the next unfetched page
      let page = fetchedPageRef.current + 1;
      let totalPages = listTotalPages;
      // Accumulate starting from current visible items
      const accum: EntrySummary[] = [...listItems];
      const seenIds = new Set(accum.map((i) => i.id));

      while (page <= totalPages) {
        const params = new URLSearchParams({
          page: String(page),
          page_size: String(PAGE_SIZE),
        });
        if (search.trim()) params.set("search", search.trim());
        if (statusFilter) params.set("status", statusFilter);

        const data = await api.entries(params);
        setTotalRawEntries(data.total);
        totalPages = data.total_pages;
        fetchedPageRef.current = data.page;
        setListPage(data.page);
        setListTotalPages(data.total_pages);

        for (const item of data.items) {
          if (!seenIds.has(item.id)) {
            accum.push(item);
            seenIds.add(item.id);
          }
        }
        // Update sidebar progressively so user sees items appear
        setListItems(prepareSidebarEntries([...accum]));
        page = data.page + 1;
      }
    } catch (e) {
      setListError(e instanceof Error ? e.message : "تعذر تحميل القائمة");
    } finally {
      setLoadingAll(false);
    }
  }, [loadingAll, loadingMore, listLoading, listItems, listTotalPages, search, statusFilter]);

  useEffect(() => {
    fetchedPageRef.current = 0;
    const timer = window.setTimeout(() => {
      void loadList(1, false);
    }, search ? 300 : 0);
    return () => window.clearTimeout(timer);
  }, [search, statusFilter, loadList]);

  const loadDetail = useCallback(
    async (id: string) => {
      setDetailLoading(true);
      setDetailError(null);
      setCandidates([]);
      try {
        const data = await api.entry(id);
        setEntry(data);
        setRejectionReason(data.rejection_reason ?? "");
        setReviewerVision(data.reviewer_vision ?? "");
        setNotes(data.notes ?? "");
        patchEntryInList(toEntrySummary(data));
        if (data.status === "needs_selection") {
          setCandidates(await api.entryImages(id));
        }
      } catch (e) {
        setDetailError(e instanceof Error ? e.message : "تعذر تحميل التفاصيل");
        setEntry(null);
        setCandidates([]);
      } finally {
        setDetailLoading(false);
      }
    },
    [patchEntryInList],
  );

  useEffect(() => {
    if (routeId) {
      setSelectedId(routeId);
      loadDetail(routeId);
    } else {
      setSelectedId(null);
      setEntry(null);
      setCandidates([]);
    }
  }, [routeId, loadDetail]);

  const selectWord = useCallback(
    (id: string) => {
      setSelectedId(id);
      setDetailError(null);
      void loadDetail(id);
      navigate(`/word/${id}`);
    },
    [loadDetail, navigate],
  );

  const startReview = async () => {
    setDetailError(null);
    try {
      const data = await api.queueNext("needs_review");
      navigate(`/word/${data.id}`);
    } catch (e) {
      setDetailError(e instanceof Error ? e.message : "لا توجد كلمات بانتظار المراجعة");
    }
  };

  const refreshAfterAction = useCallback(
    async (entryId: string, advance = false) => {
      if (advance) {
        // Patch the acted-on entry's sidebar status without resetting the list.
        // Fire-and-forget so navigation is not delayed.
        void api
          .entry(entryId)
          .then((updated) => patchEntryInList(toEntrySummary(updated)))
          .catch(() => {});
        try {
          const current = entry;
          const next = await api.queueNext(queueStatus(current), entryId, "next");
          navigate(`/word/${next.id}`);
        } catch {
          setMessage("تمت العملية — لا توجد كلمات أخرى في القائمة");
          navigate("/");
        }
      } else {
        // Stay on same entry: loadDetail fetches fresh data AND patches sidebar in place.
        await loadDetail(entryId);
      }
    },
    [entry, loadDetail, patchEntryInList, navigate],
  );

  const runAction = useCallback(
    async (entryId: string, action: () => Promise<unknown>, successMsg: string, advance = false) => {
      setBusy(true);
      setDetailError(null);
      setMessage(null);
      try {
        await action();
        setMessage(successMsg);
        setModalOpen(false);
        await refreshAfterAction(entryId, advance);
      } catch (e) {
        setDetailError(e instanceof Error ? e.message : "فشلت العملية");
        await loadDetail(entryId);
      } finally {
        setBusy(false);
      }
    },
    [loadDetail, refreshAfterAction],
  );

  const handleRegenerate = useCallback(async () => {
    if (!entry) return;
    const entryId = entry.id;
    setRegenerating(true);
    setDetailError(null);
    setMessage(null);
    try {
      await api.regenerate(entryId, {
        rejection_reason: rejectionReason.trim(),
        reviewer_vision: reviewerVision.trim() || undefined,
        notes: notes.trim() || undefined,
      });
      setMessage("تم توليد صورة جديدة");
      setModalOpen(false);
      await loadDetail(entryId);
    } catch (e) {
      setDetailError(e instanceof Error ? e.message : "فشل توليد الصورة");
      await loadDetail(entryId);
    } finally {
      setRegenerating(false);
    }
  }, [entry, loadDetail, notes, rejectionReason, reviewerVision]);

  const handleSkip = async () => {
    if (!entry) return;
    setBusy(true);
    setDetailError(null);
    try {
      const next = await api.queueNext(queueStatus(entry), entry.id, "next");
      navigate(`/word/${next.id}`);
    } catch (e) {
      setDetailError(e instanceof Error ? e.message : "لا توجد كلمات أخرى");
    } finally {
      setBusy(false);
    }
  };

  const handleSelectAndApprove = async (imageId: string) => {
    if (!entry) return;
    await runAction(entry.id, async () => {
      await api.selectImage(entry.id, imageId);
      await api.approve(entry.id);
    }, "تم اختيار الصورة واعتمادها", true);
  };

  return (
    <div className="dict-app">
      <header className="dict-header">
        <h1>مراجعة صور معجم الرياض</h1>
        <div className="dict-header-controls">
          <input
            type="search"
            className="dict-search"
            placeholder="ابحث عن كلمة..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <select
            className="dict-status-filter"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
          >
            {HEADER_FILTERS.map((f) => (
              <option key={f.value || "all"} value={f.value}>
                {f.label}
              </option>
            ))}
          </select>
        </div>
      </header>

      {listError && listItems.length === 0 && (
        <p className="dict-banner error">{listError}</p>
      )}

      <div className="dict-body">
        <WordSidebar
          items={listItems}
          selectedId={selectedId}
          loading={listLoading}
          loadingMore={loadingMore}
          loadingAll={loadingAll}
          hasMore={listPage < listTotalPages}
          totalRaw={totalRawEntries}
          onSelect={selectWord}
          onStartReview={startReview}
          onLoadMore={handleLoadMore}
          onLoadAll={handleLoadAll}
        />

        <WordDetailPanel
          entry={entry}
          candidates={candidates}
          loading={detailLoading}
          busy={busy}
          regenerating={regenerating}
          error={detailError}
          message={message}
          modalOpen={modalOpen}
          rejectionReason={rejectionReason}
          reviewerVision={reviewerVision}
          notes={notes}
          onOpenReject={() => setModalOpen(true)}
          onCloseReject={() => setModalOpen(false)}
          onChangeRejection={setRejectionReason}
          onChangeVision={setReviewerVision}
          onChangeNotes={setNotes}
          onApprove={() =>
            entry && runAction(entry.id, () => api.approve(entry.id), "تم اعتماد الصورة", true)
          }
          onSkip={handleSkip}
          onSaveReject={() =>
            entry &&
            runAction(
              entry.id,
              () =>
                api.reject(entry.id, {
                  rejection_reason: rejectionReason.trim(),
                  reviewer_vision: reviewerVision.trim() || undefined,
                  notes: notes.trim() || undefined,
                }),
              "تم حفظ الرفض",
              true,
            )
          }
          onSaveRejectAndRegenerate={() => void handleRegenerate()}
          onSelectImage={(imageId) =>
            entry && runAction(entry.id, () => api.selectImage(entry.id, imageId), "تم اختيار الصورة")
          }
          onSelectAndApprove={handleSelectAndApprove}
        />
      </div>
    </div>
  );
}
