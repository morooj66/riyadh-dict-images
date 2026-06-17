/** Remove combining diacritics for optional display hint only — never mutates stored data. */
const DIACRITICS_RE = /[\u064B-\u065F\u0670\u06D6-\u06ED]/g;

export function stripArabicDiacritics(text: string): string {
  return text.replace(DIACRITICS_RE, "");
}

export function hasArabicDiacritics(text: string): boolean {
  return stripArabicDiacritics(text) !== text;
}
