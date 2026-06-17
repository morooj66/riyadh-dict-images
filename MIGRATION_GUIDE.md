# Migration Guide — البيانات الحالية → MongoDB + Supabase

دليل خطوة بخطوة لنقل البيانات من Google Sheets الحالي إلى MongoDB Atlas + Supabase Storage بدون فقد أي شي.

---

## 0. قبل ما تبدأين — متطلبات

| الخدمة | الخطوة |
|---|---|
| **MongoDB Atlas** | أنشئي cluster مجاني (M0) من https://cloud.mongodb.com → Project → Build a Database |
| **Supabase** | مشروع جديد من https://supabase.com → New project. خذي Project URL + service_role key |
| **Google Cloud** | فعّلي Sheets API + Drive API. أنشئي Service Account → Download JSON keyfile |
| **شاركي الشيت** | أضيفي إيميل الـ Service Account بصلاحية **Viewer** على الـ spreadsheet والـ Drive folder |

---

## 1. إعداد المشروع محلياً

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate     # على ويندوز:  .venv\Scripts\activate
pip install -r requirements.txt
```

ضعي ملف الـ service account JSON في `backend/google_service_account.json`.

انسخي القالب وعبّيه:
```bash
cp .env.example .env
# عدّلي القيم في .env
```

اللي لازم تعبّينه:
- `MONGO_URI` — من Atlas → Connect → Drivers
- `MONGO_DB_NAME` — اتركيه `riyadh_dictionary` أو غيّريه
- `SUPABASE_URL` و `SUPABASE_SERVICE_KEY`
- `SHEETS_SPREADSHEET_ID` — من رابط الشيت (الجزء بين `/d/` و `/edit`)
- `SHEETS_WORKSHEET_NAME` — اسم التاب (مثلاً `Clean Sheet` أو `اسم آلة`)
- `API_KEY` — ولّدي قيمة عشوائية: `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`

---

## 2. عدّلي column mapping (مهم!)

افتحي `backend/scripts/migrate_sheets_to_mongo.py` وروحي على `COLUMN_MAP`.

القيم لازم تطابق **بالضبط** أسماء الأعمدة في الشيت. مثلاً لو عمود الكلمة في شيتك اسمه `الكلمة` بدل `word`:

```python
COLUMN_MAP = {
    "word":          "الكلمة",        # ← غيّري هذي
    "definition":    "التعريف",      # ← وهذي
    "drive_file_id": "drive_file_id",
    ...
}
```

> **نصيحة:** افتحي الشيت، انسخي اسم العمود بالضبط (حتى المسافات)، الصقيه في `COLUMN_MAP`.

---

## 3. أنشئي الـ indexes (مرة وحدة فقط)

```bash
cd backend
python3 -m scripts.setup_mongo_indexes
```

النتيجة المتوقعة: جدول جميل يعرض كل الـ indexes اللي تم إنشاؤها. **بدون هالخطوة الاستعلامات بتصير بطيئة كل ما زادت البيانات.**

---

## 4. شغّلي dry-run أولاً (آمنة 100%)

```bash
python3 -m scripts.migrate_sheets_to_mongo --dry-run --limit 10
```

هذي تطبع وش راح يصير على أول 10 صفوف **بدون ما تكتب أي شي** في Mongo أو Supabase. راجعي الـ summary:

- هل عدد الصفوف صحيح؟
- هل في أخطاء في column mapping؟
- هل الكلمات اللي تطلع لها صور صحيحة؟

---

## 5. شغّلي migration حقيقية بـ batch صغير

```bash
python3 -m scripts.migrate_sheets_to_mongo --limit 20
```

تحققي في MongoDB Atlas (Browse Collections) إن:
- في collection اسمها `entries` فيها 20 doc
- في collection اسمها `images` فيها ~20 doc
- كل entry له `current_image_id` يشير لصورة في `images`
- في Supabase Storage → bucket `dictionary-images` → folder `entries/` → فيها 20 صورة

---

## 6. شغّلي الـ migration الكاملة

```bash
python3 -m scripts.migrate_sheets_to_mongo
```

الخصائص اللي تحميك:
- **Idempotent:** لو شغّلتيها مرتين، الصفوف اللي تمت ما تتكرر (تشيك بالـ word + category)
- **Checkpoints:** كل 25 صف ينحفظ checkpoint
- **Resumable:** لو انقطع الإنترنت، شغّلي:
  ```bash
  python3 -m scripts.migrate_sheets_to_mongo --resume
  ```
- **Error tolerance:** صف فيه مشكلة (مثلاً drive_file_id خربان) ما يوقف المايقريشن — يطلع في جدول الأخطاء النهائي

---

## 7. تحققي من النتيجة النهائية

في MongoDB Atlas, شغّلي:

```javascript
// عدد الـ entries
db.entries.countDocuments()

// عدد الصور
db.images.countDocuments()

// كل entry له صورة حالية؟
db.entries.countDocuments({ current_image_id: null })   // ابغاه = 0 أو رقم متوقع
```

في Supabase → Storage → bucket → تشيكين أعداد الملفات.

---

## أسئلة شائعة

**س: لو غلطت في column mapping وبغيت أبدأ من جديد؟**  
ج: امسحي الـ database وأعيدي:
```javascript
db.dropDatabase()   // في MongoDB shell
```
ثم احذفي bucket من Supabase وأعيدي خطوة 3-6.

**س: كيف أضيف entries جديدة بعد المايقريشن؟**  
ج: المايقريشن idempotent — أضيفي صفوف جديدة للشيت وأعيدي تشغيل السكريبت، بياخذ الجديد فقط.

**س: في كم data limit؟**  
ج:
- MongoDB Atlas M0 = 512 MB (يكفي لـ ~100k entry بدون صور)
- Supabase free = 1 GB storage + 2 GB transfer/شهر
- لو زاد، نرتقي بسهولة بدون تعديل كود.

---

## بعد ما تخلصين الـ migration

الخطوة الجاية: نبني **FastAPI backend** اللي يقرأ من MongoDB ويعرض الـ endpoints للـ React reviewer. قوليلي خلصت وأبدأ.
