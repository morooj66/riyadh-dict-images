import { stripArabicDiacritics } from "../utils/arabicText";

interface Props {
  word: string;
  variant?: "title" | "sidebar";
}

/** Display-only: plain word for reviewers; diacritized form in tooltip when different. */
export function ArabicWord({ word, variant = "title" }: Props) {
  const plain = stripArabicDiacritics(word);
  const hasDiacritics = plain !== word;

  return (
    <span
      className={`arabic-word-wrap arabic-word-wrap--${variant}`}
      title={hasDiacritics ? word : undefined}
    >
      <span className={`arabic-word arabic-word--${variant}`}>{plain}</span>
    </span>
  );
}
