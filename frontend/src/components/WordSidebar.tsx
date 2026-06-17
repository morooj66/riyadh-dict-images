import type { EntrySummary } from "../api/client";
import { ArabicWord } from "./ArabicWord";
import { statusClass, statusLabel } from "../utils/statusLabels";

interface Props {
  items: EntrySummary[];
  selectedId: string | null;
  loading: boolean;
  loadingMore: boolean;
  loadingAll: boolean;
  hasMore: boolean;
  totalRaw: number;
  onSelect: (id: string) => void;
  onStartReview: () => void;
  onLoadMore: () => void;
  onLoadAll: () => void;
}

export function WordSidebar({
  items,
  selectedId,
  loading,
  loadingMore,
  loadingAll,
  hasMore,
  totalRaw,
  onSelect,
  onStartReview,
  onLoadMore,
  onLoadAll,
}: Props) {
  const hiddenCount = totalRaw > 0 ? totalRaw - items.length : 0;
  return (
    <aside className="dict-sidebar">
      <button type="button" className="sidebar-start-btn" onClick={onStartReview}>
        ابدأ المراجعة
      </button>

      {items.length > 0 && (
        <div className="sidebar-stats">
          <span className="sidebar-count">يعرض {items.length} كلمة</span>
          {hiddenCount > 0 && hasMore && (
            <span className="sidebar-hidden-note">({hiddenCount} مخفية — بدون صور أو مكررة)</span>
          )}
        </div>
      )}

      <div className="sidebar-list" role="list">
        {items.length === 0 && !loading && (
          <p className="sidebar-empty muted">لا توجد كلمات مطابقة</p>
        )}
        {items.map((entry) => (
          <button
            key={entry.id}
            type="button"
            role="listitem"
            className={`sidebar-item ${selectedId === entry.id ? "active" : ""}`}
            onClick={() => onSelect(entry.id)}
          >
            <ArabicWord word={entry.word} variant="sidebar" />
            <span className="sidebar-meta">
              {entry.category} ·{" "}
              <span className={statusClass(entry.status)}>{statusLabel(entry.status)}</span>
            </span>
          </button>
        ))}
        {loading && !loadingMore && (
          <p className="sidebar-loading muted">جاري التحميل…</p>
        )}
        {loadingMore && (
          <p className="sidebar-loading muted">جاري تحميل المزيد…</p>
        )}
        {loadingAll && (
          <p className="sidebar-loading muted">جاري تحميل كل الكلمات…</p>
        )}
      </div>

      <div className="sidebar-footer-btns">
        {hasMore && (
          <button
            type="button"
            className="sidebar-more-btn"
            onClick={onLoadMore}
            disabled={loading || loadingMore || loadingAll}
          >
            {loadingMore ? "جاري التحميل…" : "تحميل المزيد"}
          </button>
        )}
        {hasMore && (
          <button
            type="button"
            className="sidebar-load-all-btn"
            onClick={onLoadAll}
            disabled={loading || loadingMore || loadingAll}
          >
            {loadingAll ? "جاري التحميل…" : "تحميل كل الكلمات"}
          </button>
        )}
        {!hasMore && items.length > 0 && (
          <p className="sidebar-end muted">
            ✓ تم تحميل كل الكلمات ({items.length})
          </p>
        )}
      </div>
    </aside>
  );
}
