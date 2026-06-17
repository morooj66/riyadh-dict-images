import { useEffect } from "react";
import { EntryImage } from "./EntryImage";

interface Props {
  publicUrl: string;
  driveFileId?: string | null;
  label?: string;
  onClose: () => void;
}

export function ImageLightbox({ publicUrl, driveFileId, label, onClose }: Props) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [onClose]);

  return (
    <div className="lightbox-backdrop" onClick={onClose} role="dialog" aria-modal>
      <div className="lightbox-content" onClick={(e) => e.stopPropagation()}>
        <div className="lightbox-header">
          {label && <span className="lightbox-label">{label}</span>}
          <button
            type="button"
            className="lightbox-close"
            onClick={onClose}
            aria-label="إغلاق"
          >
            ✕
          </button>
        </div>
        <EntryImage
          publicUrl={publicUrl}
          driveFileId={driveFileId}
          alt={label ?? ""}
          className="lightbox-image"
        />
      </div>
    </div>
  );
}
