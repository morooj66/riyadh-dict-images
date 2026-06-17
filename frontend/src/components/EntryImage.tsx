import { useMemo, useState } from "react";
import { isDriveImageUrl, resolveImageUrl } from "../utils/imageUrl";

interface Props {
  publicUrl?: string | null;
  driveFileId?: string | null;
  alt: string;
  className?: string;
  onClick?: () => void;
}

export function EntryImage({ publicUrl, driveFileId, alt, className = "hero-image", onClick }: Props) {
  const [failed, setFailed] = useState(false);
  const [useThumbnail, setUseThumbnail] = useState(false);

  const isDrive = useMemo(
    () => isDriveImageUrl(publicUrl, driveFileId),
    [publicUrl, driveFileId],
  );

  const src = useMemo(
    () => resolveImageUrl(publicUrl, driveFileId, useThumbnail ? "thumbnail" : "view"),
    [publicUrl, driveFileId, useThumbnail],
  );

  if (!src || failed) {
    return (
      <div className={`${className} placeholder`}>
        {isDrive
          ? "الصورة غير متاحة — تحقق من صلاحيات مشاركة Google Drive (أي شخص لديه الرابط)"
          : "الصورة غير متاحة — تحقق من الرابط"}
      </div>
    );
  }

  return (
    <img
      src={src}
      alt={alt}
      className={`${className}${onClick ? " clickable-image" : ""}`}
      loading="lazy"
      referrerPolicy="no-referrer"
      onClick={onClick}
      onError={() => {
        if (isDrive && !useThumbnail) {
          setUseThumbnail(true);
          return;
        }
        setFailed(true);
      }}
    />
  );
}
