#!/usr/bin/env python3
"""Check Drive image accessibility from MongoDB records (stats only, no URLs logged)."""
from __future__ import annotations

import argparse
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.utils import extract_drive_file_id, resolve_public_image_url  # noqa: E402


def check_image(url: str) -> str:
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            content_type = (resp.headers.get("Content-Type") or "").lower()
            if "image" in content_type:
                return "ok"
            return "not_image"
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return "forbidden"
        if exc.code == 404:
            return "not_found"
        return "http_error"
    except Exception:
        return "error"


def main() -> None:
    parser = argparse.ArgumentParser(description="Check Drive image access from MongoDB")
    parser.add_argument("--limit", type=int, default=50, help="Max images to check (0 = all)")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    uri = os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017")
    db_name = os.environ.get("MONGO_DB_NAME", "riyadh_dictionary")

    db = MongoClient(uri, serverSelectionTimeoutMS=5000)[db_name]
    cursor = db.images.find({}, {"public_url": 1, "drive_file_id": 1})
    if args.limit > 0:
        cursor = cursor.limit(args.limit)

    stats = {
        "checked": 0,
        "ok": 0,
        "forbidden": 0,
        "not_found": 0,
        "not_image": 0,
        "no_reference": 0,
        "other": 0,
    }

    for doc in cursor:
        drive_id = doc.get("drive_file_id") or extract_drive_file_id(doc.get("public_url"))
        if not drive_id and not doc.get("public_url"):
            stats["no_reference"] += 1
            continue

        stats["checked"] += 1
        url = resolve_public_image_url(doc.get("public_url"), drive_id)
        if not url:
            stats["no_reference"] += 1
            continue

        result = check_image(url)
        if result == "ok":
            stats["ok"] += 1
        elif result == "forbidden":
            stats["forbidden"] += 1
        elif result == "not_found":
            stats["not_found"] += 1
        elif result == "not_image":
            stats["not_image"] += 1
        else:
            stats["other"] += 1

    total = db.images.count_documents({})
    print(f"total_images_in_db={total}")
    print(f"checked={stats['checked']}")
    print(f"accessible={stats['ok']}")
    print(f"forbidden={stats['forbidden']}")
    print(f"not_found={stats['not_found']}")
    print(f"not_image_response={stats['not_image']}")
    print(f"no_url_or_drive_id={stats['no_reference']}")
    print(f"other_errors={stats['other']}")

    if stats["forbidden"] > 0:
        print("hint=some files may need 'Anyone with the link' sharing on Google Drive")
        sys.exit(1)


if __name__ == "__main__":
    main()
