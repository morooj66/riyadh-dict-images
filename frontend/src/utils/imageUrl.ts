const DRIVE_ID_PATTERNS = [
  /[?&]id=([a-zA-Z0-9_-]+)/,
  /\/file\/d\/([a-zA-Z0-9_-]+)/,
  /\/d\/([a-zA-Z0-9_-]+)/,
];

export function extractDriveFileId(value: string | null | undefined): string | null {
  if (!value?.trim()) return null;
  const text = value.trim();
  for (const pattern of DRIVE_ID_PATTERNS) {
    const match = text.match(pattern);
    if (match?.[1]) return match[1];
  }
  return text.length >= 20 && !text.includes("/") ? text : null;
}

/** Browser-friendly URL; Drive links use export=view or thumbnail fallback. */
export function resolveImageUrl(
  publicUrl: string | null | undefined,
  driveFileId?: string | null,
  format: "view" | "thumbnail" = "view",
): string | null {
  const fileId = driveFileId || extractDriveFileId(publicUrl);
  if (fileId) {
    if (format === "thumbnail") {
      return `https://drive.google.com/thumbnail?id=${fileId}&sz=w1000`;
    }
    return `https://drive.google.com/uc?export=view&id=${fileId}`;
  }
  return publicUrl?.trim() || null;
}

export function isDriveImageUrl(
  publicUrl: string | null | undefined,
  driveFileId?: string | null,
): boolean {
  return Boolean(driveFileId || extractDriveFileId(publicUrl));
}
