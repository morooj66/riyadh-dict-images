import type { EntrySummary } from "../api/client";
import { stripArabicDiacritics } from "./arabicText";

const STATUS_RANK: Record<string, number> = {
  needs_selection: 5,
  needs_review: 4,
  approved: 3,
  generation_failed: 2,
  rejected: 1,
  generating: 0,
  pending: 0,
};

function entryKey(item: EntrySummary): string {
  return `${stripArabicDiacritics(item.word)}\0${item.category}`;
}

function entryScore(item: EntrySummary): number {
  const status = STATUS_RANK[item.status] ?? 0;
  const hasImage = item.has_image ? 1 : 0;
  const imageCount = item.image_count ?? 0;
  const updated = new Date(item.updated_at).getTime() || 0;
  return status * 1e15 + hasImage * 1e12 + imageCount * 1e9 + updated;
}

/** Entry has a reviewable image (current or any linked image). */
export function hasReviewableImage(item: EntrySummary): boolean {
  return item.has_image || (item.image_count ?? 0) > 0;
}

/** Keep one entry per plain word + category (best status / image / count / date). */
export function dedupeEntries(items: EntrySummary[]): EntrySummary[] {
  const winners = new Map<string, EntrySummary>();
  const order: string[] = [];

  for (const item of items) {
    const key = entryKey(item);
    const current = winners.get(key);
    if (!current) {
      order.push(key);
      winners.set(key, item);
      continue;
    }
    if (entryScore(item) > entryScore(current)) {
      winners.set(key, item);
    }
  }

  return order.map((key) => winners.get(key)!);
}

/** Sidebar list: dedupe then hide entries without reviewable images. */
export function prepareSidebarEntries(items: EntrySummary[]): EntrySummary[] {
  return dedupeEntries(items).filter(hasReviewableImage);
}
