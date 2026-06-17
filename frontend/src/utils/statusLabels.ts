export const STATUS_LABELS: Record<string, string> = {
  pending: "جديد",
  needs_review: "بانتظار المراجعة",
  rejected: "مرفوض",
  generating: "جاري التوليد",
  needs_selection: "بانتظار اختيار الصورة",
  approved: "معتمد",
  generation_failed: "فشل التوليد",
  candidate: "مرشحة",
  selected: "مختارة",
  current: "الحالية",
};

export const HEADER_FILTERS = [
  { value: "", label: "الكل" },
  { value: "needs_review", label: "بانتظار المراجعة" },
  { value: "needs_selection", label: "بانتظار اختيار صورة" },
  { value: "rejected", label: "مرفوضة" },
  { value: "approved", label: "معتمدة" },
] as const;

export function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status;
}

export function statusClass(status: string): string {
  return `badge badge-${status.replace(/[^a-z_]/g, "")}`;
}
