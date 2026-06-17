interface Props {
  open: boolean;
  rejectionReason: string;
  reviewerVision: string;
  notes: string;
  busy: boolean;
  regenerating: boolean;
  error: string | null;
  onChangeRejection: (v: string) => void;
  onChangeVision: (v: string) => void;
  onChangeNotes: (v: string) => void;
  onClose: () => void;
  onSaveReject: () => void;
  onSaveRejectAndRegenerate: () => void;
}

export function RejectModal({
  open,
  rejectionReason,
  reviewerVision,
  notes,
  busy,
  regenerating,
  error,
  onChangeRejection,
  onChangeVision,
  onChangeNotes,
  onClose,
  onSaveReject,
  onSaveRejectAndRegenerate,
}: Props) {
  if (!open) return null;

  const canSubmit = Boolean(rejectionReason.trim() || reviewerVision.trim());
  const modalBusy = busy || regenerating;

  return (
    <div className="modal-backdrop" onClick={modalBusy ? undefined : onClose}>
      <div className="modal-panel" onClick={(e) => e.stopPropagation()}>
        <h2>رفض الصورة</h2>
        {error && <p className="error modal-error">{error}</p>}
        <label>
          سبب الرفض <span className="required">*</span>
          <textarea
            value={rejectionReason}
            onChange={(e) => onChangeRejection(e.target.value)}
            rows={3}
            placeholder="مثال: الشكل غير دقيق، الزاوية خاطئة…"
          />
        </label>
        <label>
          تصور المراجع للصورة المطلوبة
          <textarea
            value={reviewerVision}
            onChange={(e) => onChangeVision(e.target.value)}
            rows={3}
            placeholder="مثال: زاوية 3/4، خلفية محايدة…"
          />
        </label>
        <label>
          ملاحظة إضافية
          <textarea
            value={notes}
            onChange={(e) => onChangeNotes(e.target.value)}
            rows={2}
          />
        </label>
        <div className="modal-actions">
          <button type="button" disabled={modalBusy} onClick={onClose}>
            إلغاء
          </button>
          <button type="button" disabled={modalBusy || !rejectionReason.trim()} onClick={onSaveReject}>
            حفظ الرفض
          </button>
          <button
            type="button"
            className="btn-primary"
            disabled={modalBusy || !canSubmit}
            onClick={onSaveRejectAndRegenerate}
          >
            {regenerating ? "جاري التوليد…" : "حفظ الرفض وإعادة توليد"}
          </button>
        </div>
      </div>
    </div>
  );
}
