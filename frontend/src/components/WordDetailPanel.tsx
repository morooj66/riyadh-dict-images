import { useState } from "react";
import type { EntryDetail, ImageSummary } from "../api/client";
import { ArabicWord } from "./ArabicWord";
import { EntryImage } from "./EntryImage";
import { ImageLightbox } from "./ImageLightbox";
import { RejectModal } from "./RejectModal";
import { statusClass, statusLabel } from "../utils/statusLabels";

function imageDisplayLabel(img: ImageSummary, fallback = "صورة"): string {
  const lbl = img.generation_label;

  // Explicit label takes priority
  if (lbl) {
    if (lbl === "original") return "الأصلية";
    const m = lbl.match(/^regenerate_(\d+)$/);
    if (m) return `محاولة التوليد ${m[1]}`;
    return lbl;
  }

  // No label yet — infer from other fields (before backfill is applied)
  if (img.image_role === "original") return "الأصلية";
  if (img.generated_by === "fastapi") return fallback !== "صورة" ? fallback : "مرشحة (بدون تسمية)";

  // Default: truly unknown → use fallback passed by caller
  return fallback;
}

interface Props {
  entry: EntryDetail | null;
  candidates: ImageSummary[];
  loading: boolean;
  busy: boolean;
  regenerating: boolean;
  error: string | null;
  message: string | null;
  modalOpen: boolean;
  rejectionReason: string;
  reviewerVision: string;
  notes: string;
  onOpenReject: () => void;
  onCloseReject: () => void;
  onChangeRejection: (v: string) => void;
  onChangeVision: (v: string) => void;
  onChangeNotes: (v: string) => void;
  onApprove: () => void;
  onSkip: () => void;
  onSaveReject: () => void;
  onSaveRejectAndRegenerate: () => void;
  onSelectImage: (imageId: string) => void;
  onSelectAndApprove: (imageId: string) => void;
}

export function WordDetailPanel({
  entry,
  candidates,
  loading,
  busy,
  regenerating,
  error,
  message,
  modalOpen,
  rejectionReason,
  reviewerVision,
  notes,
  onOpenReject,
  onCloseReject,
  onChangeRejection,
  onChangeVision,
  onChangeNotes,
  onApprove,
  onSkip,
  onSaveReject,
  onSaveRejectAndRegenerate,
  onSelectImage,
  onSelectAndApprove,
}: Props) {
  const [lightbox, setLightbox] = useState<{
    publicUrl: string;
    driveFileId?: string | null;
    label: string;
  } | null>(null);

  if (!entry && loading) {
    return <div className="dict-detail empty">جاري التحميل…</div>;
  }

  if (!entry) {
    return (
      <div className="dict-detail empty">
        {error ? <p className="error">{error}</p> : <p>اختر كلمة من القائمة لعرض تفاصيلها</p>}
      </div>
    );
  }

  const showCandidates = entry.status === "needs_selection" && candidates.length > 0;
  const visualDescription = entry.object_description ?? "—";
  const promptText = entry.base_prompt ?? entry.current_image?.prompt ?? "—";

  return (
    <section className="dict-detail">
      <header className="detail-header">
        <h1 className="detail-word-title">
          <ArabicWord word={entry.word} variant="title" />
        </h1>
        <span className={statusClass(entry.status)}>{statusLabel(entry.status)}</span>
      </header>

      <div className="detail-body">
        <div className="detail-info">
          <dl className="meta-list">
            <div>
              <dt>التعريف</dt>
              <dd className="arabic-text">{entry.definition ?? "—"}</dd>
            </div>
            <div>
              <dt>التصنيف</dt>
              <dd>{entry.category}</dd>
            </div>
            <div>
              <dt>الوصف البصري</dt>
              <dd className="text-block ltr-text">{visualDescription}</dd>
            </div>
            <div>
              <dt>البرومت</dt>
              <dd className="text-block prompt-block ltr-text">{promptText}</dd>
            </div>
            <div>
              <dt>عدد المحاولات</dt>
              <dd>{entry.image_count}</dd>
            </div>
          </dl>

          {entry.rejection_reason && (
            <p className="note-box arabic-text">
              <strong>سبب الرفض:</strong> {entry.rejection_reason}
            </p>
          )}
          {entry.notes && (
            <p className="note-box arabic-text">
              <strong>ملاحظة المراجع:</strong> {entry.notes}
            </p>
          )}

          <div className="detail-actions">
            <button
              type="button"
              className="btn-success"
              disabled={busy || (!entry.selected_image_id && !entry.current_image_id)}
              onClick={onApprove}
            >
              اعتماد
            </button>
            <button type="button" disabled={busy || regenerating} onClick={onOpenReject}>
              رفض
            </button>
            <button type="button" disabled={busy || regenerating} onClick={onOpenReject}>
              إعادة توليد
            </button>
            <button type="button" disabled={busy || regenerating} onClick={onSkip}>
              تخطي
            </button>
          </div>
        </div>

        <div className="detail-image-card">
          <h2>الصورة الحالية</h2>
          {entry.current_image ? (
            <>
              <EntryImage
                publicUrl={entry.current_image.public_url}
                driveFileId={entry.current_image.drive_file_id}
                alt={entry.word}
                className="hero-image"
                onClick={() =>
                  setLightbox({
                    publicUrl: entry.current_image!.public_url,
                    driveFileId: entry.current_image!.drive_file_id,
                    label: imageDisplayLabel(entry.current_image!, "الصورة الحالية"),
                  })
                }
              />
              <p className="image-zoom-hint">اضغط على الصورة لتكبيرها</p>
            </>
          ) : (
            <div className="hero-image placeholder">لا توجد صورة</div>
          )}
        </div>
      </div>

      {showCandidates && (
        <section className="candidates-section">
          <h2>الصور المرشحة</h2>
          <div className="image-grid">
            {candidates.map((img) => (
              <div key={img.id} className={`image-card ${img.is_selected ? "selected" : ""}`}>
                <EntryImage
                  publicUrl={img.public_url}
                  driveFileId={img.drive_file_id}
                  alt=""
                  className="candidate-image"
                  onClick={() =>
                    setLightbox({
                      publicUrl: img.public_url,
                      driveFileId: img.drive_file_id,
                      label: imageDisplayLabel(img, "مرشحة"),
                    })
                  }
                />
                <p className="image-zoom-hint" style={{ fontSize: "0.72rem" }}>
                  اضغط لتكبير
                </p>
                <div className="image-meta">
                  <span className="badge badge-needs_selection">
                    {imageDisplayLabel(img, "مرشحة")}
                  </span>
                  {img.is_current && <span className="badge">الحالية</span>}
                  {img.is_selected && <span className="badge badge-selected">مختارة</span>}
                </div>
                <div className="image-card-actions">
                  <button type="button" disabled={busy} onClick={() => onSelectImage(img.id)}>
                    اختيار هذه الصورة
                  </button>
                  <button
                    type="button"
                    className="btn-success"
                    disabled={busy}
                    onClick={() => onSelectAndApprove(img.id)}
                  >
                    اختيار واعتماد
                  </button>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {message && <p className="success">{message}</p>}
      {error && <p className="error">{error}</p>}

      <RejectModal
        open={modalOpen}
        rejectionReason={rejectionReason}
        reviewerVision={reviewerVision}
        notes={notes}
        busy={busy}
        regenerating={regenerating}
        error={error}
        onChangeRejection={onChangeRejection}
        onChangeVision={onChangeVision}
        onChangeNotes={onChangeNotes}
        onClose={onCloseReject}
        onSaveReject={onSaveReject}
        onSaveRejectAndRegenerate={onSaveRejectAndRegenerate}
      />

      {lightbox && (
        <ImageLightbox
          publicUrl={lightbox.publicUrl}
          driveFileId={lightbox.driveFileId}
          label={lightbox.label}
          onClose={() => setLightbox(null)}
        />
      )}
    </section>
  );
}
