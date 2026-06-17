"""Read Sheet1 header row and suggest COLUMN_MAP — no migration, no secrets printed."""
from __future__ import annotations

import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from app.config import get_settings
from scripts.migrate_sheets_to_mongo import COLUMN_MAP, get_sheets_client

console = Console()

# MongoDB field → hints for auto-matching sheet headers (lowercase substrings)
FIELD_HINTS: dict[str, list[str]] = {
    "word": ["كلمة", "word", "lemma", "مدخل", "المدخل"],
    "definition": ["تعريف", "definition", "معنى", "sense", "المعنى"],
    "category": ["تصنيف", "category", "فئة", "نوع", "class", "الفئة"],
    "object_description": ["وصف بصري", "object_description", "visual description", "وصف الشيء", "الوصف البصري"],
    "image_prompt": ["image_prompt", "برومت", "prompt", "البرومت", "وصف الصورة"],
    "drive_file_id": ["drive_file_id", "file_id", "drive id", "معرف drive", "معرف الملف"],
    "image_url": ["image_url", "رابط الصورة", "رابط", "url", "link", "صورة", "image link"],
    "status": ["status", "حالة", "state", "الحالة"],
    "rejection_reason": ["rejection", "رفض", "سبب الرفض", "سبب"],
    "reviewer_note": ["reviewer_note", "ملاحظة المراجع", "ملاحظ", "note", "comment", "ملاحظات"],
}

REQUIRED_FIELDS = ["word"]
IMAGE_FIELDS = ["drive_file_id", "image_url"]


def suggest_mapping(headers: list[str]) -> dict[str, str | None]:
    used: set[str] = set()
    mapping: dict[str, str | None] = {}

    for field, hints in FIELD_HINTS.items():
        match = None
        for h in headers:
            if not h or h in used:
                continue
            hl = h.strip().lower()
            for hint in hints:
                hint_l = hint.lower()
                if hint_l in hl or hl in hint_l:
                    match = h
                    break
            if match:
                break
        if match:
            used.add(match)
        mapping[field] = match

    return mapping


def main() -> int:
    settings = get_settings()
    sa = Path(settings.google_sa_keyfile)
    if not sa.is_absolute():
        sa = Path.cwd() / sa
    if not sa.is_file():
        console.print("[red]GOOGLE_SA_KEYFILE غير موجود — ضع google_service_account.json في backend/[/red]")
        return 1
    if not settings.sheets_spreadsheet_id:
        console.print("[red]SHEETS_SPREADSHEET_ID غير مضبوط في .env[/red]")
        return 1

    ws_name = settings.sheets_worksheet_name or "Sheet1"
    gc = get_sheets_client()
    sh = gc.open_by_key(settings.sheets_spreadsheet_id)
    ws = sh.worksheet(ws_name)
    headers = [h.strip() for h in ws.row_values(1) if h and h.strip()]

    console.print(f"\n[bold cyan]أعمدة {ws_name}[/bold cyan] ({len(headers)} عمود)\n")
    for i, h in enumerate(headers, 1):
        console.print(f"  {i:>2}. {h}")

    suggested = suggest_mapping(headers)

    table = Table(title="COLUMN_MAP مقترح", show_lines=True)
    table.add_column("حقل MongoDB", style="cyan")
    table.add_column("عمود الشيت", style="green")
    table.add_column("الحالي في الكود", style="dim")

    for field in FIELD_HINTS:
        table.add_row(
            field,
            suggested.get(field) or "[red]—[/red]",
            COLUMN_MAP.get(field, "—"),
        )

    console.print()
    console.print(table)

    console.print("\n[bold]نسخة Python للموافقة:[/bold]")
    console.print("COLUMN_MAP = {")
    for field in FIELD_HINTS:
        col = suggested.get(field)
        val = repr(col) if col else "None  # غير موجود"
        console.print(f'    "{field}": {val},')
    console.print("}")

    missing_word = not suggested.get("word")
    missing_image = not (suggested.get("drive_file_id") or suggested.get("image_url"))

    if missing_word:
        console.print("\n[red]الكلمة العربية[/red]")
    if missing_image:
        console.print("\n[red]رابط الصورة[/red]")

    console.print("\n[dim]لم يتم تشغيل migration — للمعاينة فقط.[/dim]")
    return 0 if not (missing_word or missing_image) else 1


if __name__ == "__main__":
    sys.exit(main())
