# خطة الانتقال من Cloudflare D1 إلى Supabase — مستودع `faselhd-db`
> الوثيقة الرسمية للهجرة | الإصدار 1.0 | 2026-09-17
> النطاق: قراءة تحليلية لملفات المستودع البرمجية فقط (`*.py` + `.github/workflows/*`) دون المساس بأي ملف موجود، ودون مراجعة ملفات `*.html` أو `*.md` القديمة.
> الهدف: **إيقاف D1 نهائيًا** والانتقال إلى **Supabase (Postgres)** مع الحفاظ على **الترتيب المتفق عليه**، وتحديث Supabase **بطلب واحد / دفعة واحدة** عند انتهاء GitHub Action، وبكفاءة بحث **مساوية أو أفضل** من D1، وبأحدث أدوات وتقنيات Supabase الرسمية (2026).

---

## 1) ملخص تنفيذي (Executive Summary)

| البند | الوضع الحالي (D1) | الوضع المستهدف (Supabase) |
|---|---|---|
| قاعدة البيانات | Cloudflare D1 (SQLite موزع) — اسم القاعدة `faselhd-db` | Supabase Postgres (مشروع واحد، سكيمة `public`) |
| الكتابة من CI | `d1_push.py` عبر `wrangler d1 execute --remote --file chunk_XXX.sql` بمقاطع 500 عبارة | سكربت جديد `supabase_push.py` عبر `supabase-py` + PostgREST `upsert(..., on_conflict="section_key,slug")` بدفعات 500–1000 صف في **طلب HTTP واحد لكل دفعة** |
| القراءة/البحث | `SELECT ... WHERE section_key=? ORDER BY ord ASC` + `INSERT OR IGNORE` | PostgREST: `?section_key=eq.movies&order=ord.asc` + فهرس مركب + بحث `pg_trgm` + `tsvector` + دالة `search_items()` عبر RPC |
| الترتيب | عمود `ord` عددي: FULL يبدأ من 1 تصاعديًا، DELTA يحجز `MIN(ord)-total` ثم يتزايد | **نفس المنطق حرفيًا** يُنقل إلى Postgres (`ord BIGINT`) + قيد فريد + `ON CONFLICT DO NOTHING` — لا تغيير سلوكي |
| GitHub Action | خطوة `wrangler@4` + أسرار `CLOUDFLARE_*` | تُحذف خطوة wrangler نهائيًا، وتُستبدل بخطوة `pip install supabase` + `python supabase_push.py` + سرّين فقط |
| المفاتيح | `CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_ACCOUNT_ID` | `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY` (أو `sb_secret_...` الجديد) — للكتابة من CI فقط |

**قرار معماري:** Supabase هو **مصدر الحقيقة التشغيلي** للقراءة والبحث. ملفات `output/*.json` في مستودع `faselhd-db` تبقى **أرشيف Git + مصدر الدلتا**، لكنها ليست قاعدة التشغيل. لا مزامنة ثنائية الاتجاه.

---

## 2) تحليل الوضع الحالي بدقة (As-Is — من الكود الفعلي)

تم فحص الملفات التالية فقط: `update.py` / `d1_push.py` / `github_push.py` / `telegram_notify.py` / `run.py` / `deploy.py` / `faselhd_scraper/*` / `.github/workflows/update.yml`.

### 2.1 خط الإنتاج (Pipeline)

```
cron كل 30 دقيقة (update.yml)
  └─> python update.py
        ├─ لكل قسم من SECTIONS الثمانية:
        │    movies / series / anime / asian-series / asian-movies / hindi / anime-movies / tvshows
        ├─ يجلب pages عبر fetch_page() (parsel على #postList .postDiv)
        ├─ يقارن slug مع output/<section>.json
        ├─ الجديد يوضع في المقدمة + rank جديد + added_at=UTC الآن
        ├─ القديم يُزاح rank بمقدار len(new)
        ├─ يكتب output/<section>.json كاملة + output/.delta/<section>.json (الجديد فقط)
        └─ يستدعي github_push.py --sections <المتغيرة فقط> (commit + push لملفات JSON + update_log.txt)
  └─> python d1_push.py          <-- تُحذف
  └─> python telegram_notify.py  <-- تبقى
```

### 2.2 منطق الترتيب المتفق عليه (يجب الحفاظ عليه حرفيًا)

المصدران: `d1_push.py:99-148` و `update.py:205-214`.

1. ملف JSON مرتب **الأحدث أولًا** (`rank=1` هو الأحدث). `update.py` يبني `db["items"] = all_new_items + db["items"]`.
2. عمود `ord` في D1 هو **المرآة الرقمية للترتيب**:
   - **FULL (`--full`):** `ord = 1, 2, 3, ...` بنفس ترتيب JSON. العرض: `ORDER BY ord ASC`.
   - **DELTA (الوضع الدوري):** تُقرأ `current_min = MIN(ord) WHERE section_key=X` ثم `next_ord = current_min - total`، ثم تُسند قيم متزايدة لكل عنصر جديد بالترتيب. النتيجة: كل الجديد **أصغر** من أي قديم، والأحدث بينهم هو الأصغر إطلاقًا → يظهر أولًا تحت `ORDER BY ord ASC`. مثال: `MIN=1` و `total=5` → `ord = -4,-3,-2,-1,0` للأحدث→الأقدم.
3. منع التكرار: `INSERT OR IGNORE` — مفتاح عدم التكرار الضمني هو `(section_key, slug)`.
4. بعد كل دفعة: `UPDATE sections SET items_count=(SELECT COUNT(*) ...)` للأقسام المتغيرة فقط.
5. التقطيع: `STATEMENTS_PER_CHUNK=500` ملف SQL لكل 500 عبارة، يُنفذ عبر `wrangler d1 execute --remote -y --json --file=...`.

### 2.3 سكيمة D1 المستنتجة من الكود (لا يوجد ملف schema.sql في المستودع)

```sql
-- الاستنتاج الدقيق من d1_push.py:40-48,110-112,142-143
CREATE TABLE items (
  section_key TEXT NOT NULL,
  slug        TEXT NOT NULL,
  name        TEXT NOT NULL,
  img         TEXT NOT NULL,
  link        TEXT NOT NULL,
  added_at    TEXT,              -- ISO-8601 "YYYY-MM-DDTHH:MM:SSZ"
  ord         INTEGER NOT NULL
  -- + قيد فريد ضمني (section_key, slug) بسبب INSERT OR IGNORE
);
CREATE TABLE sections (
  key         TEXT PRIMARY KEY,
  items_count INTEGER NOT NULL DEFAULT 0
);
```

### 2.4 تنسيق البيانات الفعلي (JSON → صف)

عينة حقيقية من `output/tvshows.json`:

```json
{
  "section": "tvshows",
  "base_url": "https://www.fasel-hd.cam",
  "scraped_at": "2026-09-04T13:17:57Z",
  "total": 175,
  "items": [{
    "section_key": "tvshows", "rank": 1,
    "slug": "برنامج-earle-meets-world",
    "name": "برنامج Earle Meets World",
    "img": "https://static.faselhdcdn.com/...jpg",
    "link": "https://www.fasel-hd.cam/tvseasons/...",
    "added_at": "2026-09-04T13:17:57Z"
  }]
}
```

ملاحظات حرجة للهجرة:
- `rank` **مؤقت/عرضي** داخل JSON فقط — لا يُخزن في D1 ولا يجب تخزينه في Supabase (يُعاد اشتقاقه من `ord`).
- `.delta/*.json` بنية `{section, items[]}` بدون `total` — هي **مدخل** `d1_push.py`/`supabase_push.py` في الوضع الدوري.
- `slug` قد يحتوي عربيًا وفواصل (`unquote` من URL) — يتطلب `TEXT` بترميز UTF-8 (Postgres افتراضيًا) + فهرس trigram.

---

## 3) خارطة الانتقال (To-Be Architecture)

```
┌─ faselhd-scraper (هذا المستودع) ── cron 30min ──────────────┐
│  update.py → output/*.json + .delta/*.json                  │
│  github_push.py → push إلى faselhd-db (أرشيف)                │
│  supabase_push.py (جديد) → UPSERT دفعات → Supabase Postgres │── طلب واحد/دفعة
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
              ┌─ Supabase Project ────────────────────┐
              │ public.sections (8 صفوف)               │
              │ public.items (كل الأقسام، ~عشرات آلاف)  │
              │ indexes: unique(section,slug) +        │
              │   (section,ord) + trgm(name,slug) +    │
              │   tsvector generated + GIN             │
              │ RPC: search_items(q, section, limit)   │
              │ RLS: قراءة عامة / كتابة service فقط   │
              └───────────────────────────────────────┘
                              │
              ┌───────────────┴───────────────┐
              ▼                               ▼
     موقع/تطبيق القراءة                Telegram notify
     (PostgREST + RPC مباشرة)          (تبقى كما هي)
```

**مبدأ الدفعة الواحدة:** كل `upsert([...500-1000 صف...])` = **طلب HTTP واحد** إلى `POST /rest/v1/items?on_conflict=section_key,slug`. لا حلقات INSERT مفردة. لا wrangler. لا ملفات SQL مؤقتة.

---

## 4) الروابط والمفاتيح: ما الذي نحتاجه وكيف نحصل عليه من Supabase (خطوة بخطوة — واجهة 2026)

> تنبيه 2026: Supabase يستبدل تدريجيًا `anon`/`service_role` (JWT طويلة) بمفاتيح **`sb_publishable_...`** (عام) و **`sb_secret_...`** (خادم) — القديم يعمل حتى نهاية 2026. هذه الخطة تدعم الاثنين وتوصي بالجديد.

### 4.1 الروابط الثابتة

| الغرض | الرابط |
|---|---|
| لوحة التحكم | `https://supabase.com/dashboard` |
| مشروعك | `https://supabase.com/dashboard/project/<PROJECT_REF>` |
| مفاتيح API (الجديد) | `https://supabase.com/dashboard/project/<PROJECT_REF>/settings/api-keys` |
| محرر الجداول + SQL Editor | `https://supabase.com/dashboard/project/<PROJECT_REF>/editor` و `/sql` |
| مرجع REST | `https://<PROJECT_REF>.supabase.co/rest/v1/...` |
| التوثيق الرسمي | `https://supabase.com/docs/reference/python/upsert` — `.../insert` — `https://supabase.com/docs/guides/getting-started/api-keys` — `https://supabase.com/docs/guides/database/full-text-search` |

### 4.2 المفاتيح المطلوبة (3 قيم فقط — اثنتان في CI)

| # | الاسم في CI | القيمة | أين تُستخدم | سرّي؟ |
|---|---|---|---|---|
| 1 | `SUPABASE_URL` | `https://<PROJECT_REF>.supabase.co` | كل البيئات | لا (علني آمن) |
| 2 | `SUPABASE_SERVICE_ROLE_KEY` **أو** `SUPABASE_SECRET_KEY` | `sb_secret_...` (جديد، موصى) أو JWT الـ `service_role` (قديم) | **GitHub Action للكتابة فقط** — يتجاوز RLS | **نعم — سرّي جدًا، لا يظهر في كود/فرونت** |
| 3 | (اختياري للفرونت) `SUPABASE_ANON_KEY` / `sb_publishable_...` | مفتاح القراءة العامة | موقع القراءة/التطبيق | لا (آمن للنشر مع RLS) |

**القاعدة الذهبية:** مفتاح الكتابة (`secret`/`service_role`) **لا يغادر** GitHub Secrets والسكربت الخلفي. أي تسرّب في فرونت = تدوير فوري من صفحة API Keys.

### 4.3 كيف تحصل عليها (نقرات دقيقة)

1. افتح `supabase.com/dashboard` وسجّل الدخول → اختر مشروعك (أو `New Project`).
2. انسخ **Project URL**: زر **Connect** أعلى اللوحة → تبويب/your framework → انسخ `https://<ref>.supabase.co` → هذا `SUPABASE_URL`. (البديل: `Settings → API Keys` تعرض نفس URL).
3. انسخ مفتاح الكتابة: `Settings → API Keys`:
   - مشروع جديد: تبويب **Publishable and secret API keys** → انسخ **`sb_secret_...`** (لـ CI) و **`sb_publishable_...`** (للفرونت).
   - مشروع قديم بلا مفاتيح جديدة: اضغط **Create new API keys** (آمن — لا يعطّل القديم) ثم انسخ الجديد. أو تبويب **Legacy API keys** → أظهر (`Reveal`) وانسخ `service_role` (JWT تبدأ بـ `eyJ...`).
4. تحقق سريع من الصلاحية (لا تطبع المفتاح في السجلات):
   ```bash
   curl -s -o /dev/null -w "%{http_code}\n" \
     -H "apikey: <SECRET>" -H "Authorization: Bearer <SECRET>" \
     "https://<PROJECT_REF>.supabase.co/rest/v1/sections?select=key&limit=1"
   # 200 = سليم | 401 = مفتاح خاطئ | 404 = جدول sections غير موجود بعد
   ```
5. ضعها في GitHub: مستودع `faselhd-db` (أو scraper — حسب أين يعمل الـ Action) → `Settings → Secrets and variables → Actions → New repository secret` → أنشئ `SUPABASE_URL` و `SUPABASE_SERVICE_ROLE_KEY`. **احذف لاحقًا** `CLOUDFLARE_API_TOKEN` و `CLOUDFLARE_ACCOUNT_ID` بعد نجاح أول تشغيل (البند 9).

> ربط Supabase بالمستودع الذي ذكرته (Integrations) لا يغني عن الخطوات أعلاه — الربط يمنح صلاحيات إدارة، أما الـ Action فيحتاج السرّين صراحة.

---

## 5) هيكلة وتنسيق البيانات في Supabase (DDL النهائي — انسخ والصق في SQL Editor)

### 5.1 مبادئ التصميم

1. جدولان فقط: `sections` (بعدّادات) + `items` (البيانات) — مرآة D1 بلا زيادة.
2. عدم التكرار بقيد حقيقي `UNIQUE(section_key, slug)` ليستقبل `on_conflict` (مقابل `INSERT OR IGNORE`).
3. `ord BIGINT` (وليس INT) لاستيعاب القيم السالبة المتناقصة للدلتا إلى ما لا نهاية.
4. `added_at TIMESTAMPTZ` (بدل TEXT في D1) — فرز زمني صحيح + توافق ISO-8601.
5. `search_tsv TSVECTOR GENERATED ALWAYS STORED` + `pg_trgm` — تفوق على D1 (`LIKE` بلا فهارس).

### 5.2 السكيمة الكاملة

```sql
-- ===== 0) الإضافات =====
create extension if not exists pg_trgm;
create extension if not exists unaccent;  -- اختياري: تجاهل التشكيل/الهمزات في البحث

-- ===== 1) جدول الأقسام =====
create table if not exists public.sections (
  key         text primary key,
  title       text not null default '',
  items_count integer not null default 0,
  updated_at  timestamptz not null default now(),
  constraint sections_key_check check (key in
    ('movies','series','anime','asian-series','asian-movies','hindi','anime-movies','tvshows'))
);

insert into public.sections (key, title) values
  ('movies','أفلام'), ('series','مسلسلات'), ('anime','أنمي'),
  ('asian-series','مسلسلات آسيوية'), ('asian-movies','أفلام آسيوية'),
  ('hindi','هندي'), ('anime-movies','أفلام أنمي'), ('tvshows','برامج تلفزيونية')
on conflict (key) do nothing;

-- ===== 2) جدول العناصر =====
create table if not exists public.items (
  id          bigint generated always as identity primary key,
  section_key text not null references public.sections(key) on update cascade,
  slug        text not null,
  name        text not null,
  img         text not null,
  link        text not null,
  added_at    timestamptz not null default now(),
  ord         bigint not null,
  search_tsv  tsvector generated always as (
    setweight(to_tsvector('simple', coalesce(name,'')), 'A') ||
    setweight(to_tsvector('simple', coalesce(slug,'')), 'B')
  ) stored,
  constraint items_section_slug_unique unique (section_key, slug),
  constraint items_slug_not_empty check (char_length(slug) > 0)
);

-- ===== 3) الفهارس (الأداء) =====
create index if not exists items_section_ord_idx
  on public.items (section_key, ord asc);
create index if not exists items_added_at_idx
  on public.items (section_key, added_at desc);
create index if not exists items_name_trgm_idx
  on public.items using gin (name gin_trgm_ops);
create index if not exists items_slug_trgm_idx
  on public.items using gin (slug gin_trgm_ops);
create index if not exists items_search_tsv_idx
  on public.items using gin (search_tsv);

-- ===== 4) عدّاد الأقسام التلقائي (بديل UPDATE sections ... COUNT) =====
create or replace function public.refresh_section_count()
returns trigger language plpgsql as $$
begin
  update public.sections
     set items_count = (select count(*) from public.items where section_key = coalesce(new.section_key, old.section_key)),
         updated_at = now()
   where key = coalesce(new.section_key, old.section_key);
  return coalesce(new, old);
end $$;

drop trigger if exists trg_refresh_section_count on public.items;
create trigger trg_refresh_section_count
after insert or delete on public.items
for each row execute function public.refresh_section_count();
```

**لماذا `to_tsvector('simple')` وليس `'english'`؟** المحتوى عربي/إنجليزي مختلط وأسماء أعلام — قاموس `english` يجتثّ (stem) الإنجليزية ويكسر العربية. `simple` يجزّئ بدون اجتثاث → أدق لهذا النوع من الكتالوجات، ويُستكمل بـ `pg_trgm` للبحث الجزئي والتسامح مع الأخطاء. (مرجع: `supabase.com/docs/guides/database/full-text-search`).

### 5.3 خريطة التحويل JSON → صف Postgres (لكل عنصر)

| حقل JSON | عمود Postgres | التحويل |
|---|---|---|
| `section` (أو اسم الملف) | `section_key` | نص كما هو |
| `slug` | `slug` | نص UTF-8 كما هو (فارغ → يُتجاهل الصف) |
| `name` | `name` | نص كما هو |
| `img` | `img` | URL كما هو |
| `link` | `link` | URL كما هو |
| `added_at` | `added_at` | ISO-8601 `...Z` → `timestamptz` مباشرة (غيابها → `now()`) |
| — (مشتق) | `ord` | **يُحسب بنفس خوارزمية D1** (البند 5.4) |
| `rank` | — | **يُتجاهل** (يُشتق من `ord` عند القراءة) |
| — | `search_tsv` | تلقائي (generated) — لا يُرسل |

### 5.4 الحفاظ على الترتيب (خوارزمية `ord` — النسخة Postgres)

- **FULL (استيراد أولي `output/*.json`):** رتّب عناصر القسم بنفس ترتيب المصفوفة، أسند `ord=1..N`. (مطابق `d1_push.py --full`).
- **DELTA (التشغيل الدوري `output/.delta/*.json`):** لكل قسم: اقرأ `MIN(ord)` باستعلام واحد، ثم `base = MIN - len(new)`، وأسند `base..base+len-1` بالترتيب، ثم `upsert(..., on_conflict="section_key,slug", ignore_duplicates=false)` مع `ON CONFLICT DO NOTHING` (مكافئ `INSERT OR IGNORE` — الصف الموجود لا يتحرك ولا يغيّر `ord`).
- **القراءة دائمًا:** `order=ord.asc` → الأحدث أولًا، مطابِق لسلوك D1.

---

## 6) آلية البحث — بنفس كفاءة D1 وأفضل (كيف تعمل فعليًا)

### 6.1 ما كان يفعله D1

- قائمة قسم: `SELECT * FROM items WHERE section_key=? ORDER BY ord ASC LIMIT ? OFFSET ?`
- بحث بالاسم: `... AND name LIKE '%...%'` (مسح تسلسلي بلا فهرس نصي — بطيء مع النمو).
- بلا ترتيب صلة (relevance) وبلا تسامح مع الأخطاء الإملائية.

### 6.2 ما يفعله Supabase (4 مستويات، من الأرخص للأغنى)

**المستوى 1 — قوائم الأقسام (PostgREST مباشرة، بدون كود خادم):**
```http
GET /rest/v1/items?section_key=eq.movies&select=slug,name,img,link,added_at,ord&order=ord.asc&limit=24&offset=0
```
الفهرس `items_section_ord_idx` يجعلها قراءة فهرس خالصة (أسرع من D1).

**المستوى 2 — بحث جزئي سريع (trigram، يتسامح مع الخطأ والهمزات):**
```http
GET /rest/v1/items?section_key=eq.movies&or=(name.ilike.*ناروتو*,slug.ilike.*ناروتو*)&order=ord.asc&limit=25
```
يخدمه `items_name_trgm_idx` / `items_slug_trgm_idx` (GIN) — ما كان مستحيلًا بكفاءة في D1.

**المستوى 3 — بحث نصي مرتّب بالصلة (FTS + trigram هجين عبر RPC — الموصى للإنتاج):**
```sql
create or replace function public.search_items(
  q text, sec text default null, limit_count int default 25, offset_count int default 0
)
returns table (section_key text, slug text, name text, img text, link text,
               added_at timestamptz, ord bigint, score real)
language sql stable as $$
  with params as (
    select websearch_to_tsquery('simple', q) as tsq, greatest(length(trim(q)),0) as qlen
  )
  select i.section_key, i.slug, i.name, i.img, i.link, i.added_at, i.ord,
    (ts_rank_cd(i.search_tsv, p.tsq) +
     least(greatest(similarity(i.name, q), similarity(i.slug, q)), 0.60)
       * case when p.qlen >= 5 then 0.6 else 0.2 end)::real as score
  from public.items i cross join params p
  where (sec is null or i.section_key = sec)
    and (i.search_tsv @@ p.tsq or similarity(i.name, q) > 0.25 or similarity(i.slug, q) > 0.25)
  order by score desc, i.ord asc
  limit limit_count offset offset_count;
$$;
```
الاستدعاء من أي عميل (أحدث `supabase-js`/`supabase-py`):
```js
const { data } = await supabase.rpc('search_items', { q: 'ناروتو', sec: 'anime', limit_count: 25 });
```
ملاحظة تقنية: `websearch_to_tsquery` (وليس `to_tsquery`) لأنه يتحمل مدخلات بشرية (`c++`, أقواس) دون أخطاء صياغة.

**المستوى 4 — إكمال تلقائي (autocomplete):**
```sql
select slug, name from public.items
 where section_key = 'movies' and name % 'سبايدر'
 order by similarity(name, 'سبايدر') desc limit 8;
```
المشغّل `%` (التشابه) يستخدم فهرس trigram مباشرة.

### 6.3 جدول المقارنة

| المعيار | D1 | Supabase (هذه الخطة) |
|---|---|---|
| قائمة مرتبة | `ORDER BY ord` (بلا فهرس مركب مضمون) | نفس الترتيب + فهرس `(section_key, ord)` |
| بحث جزئي `%...%` | مسح كامل | فهرس GIN trigram (أسرع 10–100× على عشرات الآلاف) |
| ترتيب بالصلة | غير موجود | `ts_rank_cd` + تشابه هجين |
| تسامح إملائي | غير موجود | `similarity()` + `%` |
| عدّادات الأقسام | `UPDATE ... COUNT` يدوي بعد كل دفعة | Trigger تلقائي |
| التزامن | كاتب واحد عبر wrangler | كتابات ذرية متزامنة عبر `ON CONFLICT` |

### 6.4 الأمان (RLS — إلزامي قبل الإطلاق)

```sql
alter table public.sections enable row level security;
alter table public.items enable row level security;

-- قراءة عامة للجميع (بما فيه مفتاح publishable/anon)
create policy "public read sections" on public.sections for select using (true);
create policy "public read items"    on public.items    for select using (true);

-- الكتابة: مفتاح secret/service_role فقط (يتجاوز RLS أصلًا — لا سياسات insert/update/delete عامة)
-- لا تنشئ أي سياسة كتابة anon/authenticated — الإبقاء على عدمها = المنع.
grant select on public.sections, public.items to anon, authenticated;
```

---

## 7) التحديث بطلب واحد / دفعة واحدة من GitHub Action (التصميم النهائي)

### 7.1 المتطلبات (أحدث الأدوات — 2026)

- `supabase>=2.x` (عميل Python الرسمي، PostgREST مدمج، يدعم `upsert(..., on_conflict=...)` — المرجع: `supabase.com/docs/reference/python/upsert`).
- مفاتيح `sb_secret_...` (أو `service_role` legacy) — **الكتابة من الخادم/CI فقط**.
- لا `wrangler`، لا Node، لا ملفات `.sql` مؤقتة.

`requirements.txt` (يُضاف سطر واحد):
```txt
scrapy>=2.16.0
pytest>=8.0.0
parsel>=1.9.0
supabase>=2.12.0
```

### 7.2 سكربت `supabase_push.py` — المواصفة الدقيقة (لمن سينفذها)

> تُبقي نفس واجهة `d1_push.py` (`--full` / `--keep-delta`) لتسهيل الاستبدال سطرًا بسطر في الـ workflow.

```python
# السلوك المطلوب (pseudo-final):
# 1) collect_items(full_mode): من output/*.json أو output/.delta/*.json — نفس الكود الحالي.
# 2) supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
# 3) لكل قسم:
#      rows = [تحويل العناصر إلى dict بدون rank] مع ord محسوب:
#        full:  1..N
#        delta: MIN(ord) من Supabase أولًا (select section_key.eq.X&select=ord&order=ord.asc&limit=1)
#               ثم base = MIN - len(rows)
#      slices بمقاس BATCH=1000 → لكل شريحة: طلب واحد:
#        supabase.table("items").upsert(rows_slice, on_conflict="section_key,slug",
#                                       ignore_duplicates=True).execute()
#      # ignore_duplicates=True ≡ INSERT OR IGNORE ≡ ON CONFLICT DO NOTHING
# 4) تحقق: count لكل قسم متغير (select count) + قارن مع total المتوقع.
# 5) عند النجاح احذف .delta (إلا مع --keep-delta). عند الفشل أبقها لل retry (نفس d1_push.py).
```

حدود عملية: PostgREST يقبل آلاف الصفوف في الطلب، لكن **500–1000** هو الأمثل (توازن زمن الاستجابة/إعادة المحاولة). الدلتا اليومية (عشرات العناصر) = **طلب واحد فعليًا لكل قسم**، وغالبًا طلب واحد لكل التشغيل.

### 7.3 الـ workflow الجديد (يستبدل خطوة D1 — انسخ والصق)

```yaml
name: Auto Update FaselHD Database

on:
  workflow_dispatch:
  schedule:
    - cron: '*/30 * * * *'

permissions:
  contents: write

jobs:
  update:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 1

      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: 'pip'

      - run: pip install -r requirements.txt

      - run: python update.py
        env:
          GIT_USER_EMAIL: ${{ secrets.GIT_USER_EMAIL }}
          GIT_USER_NAME: ${{ secrets.GIT_USER_NAME }}

      # ★★ الخطوة الجديدة: Supabase بدل D1 — طلب/دفعة واحدة ★★
      - name: Push new items to Supabase (single-request batches)
        run: python supabase_push.py
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}

      - name: Send Telegram notification
        run: python telegram_notify.py
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
          GITHUB_RUN_NUMBER: ${{ github.run_number }}

      - name: Show summary
        run: cat update_log.txt || echo "No log file"
```

الفروق عن القديم: **حُذفت** خطوتا `setup-node` و `npm install -g wrangler@4` نهائيًا، وحُذفت متغيرات `CLOUDFLARE_*` و `D1_DATABASE_NAME`.

### 7.4 الاستيراد الأولي (مرة واحدة — FULL)

```bash
# محليًا أو عبر workflow_dispatch يدوي:
python supabase_push.py --full
# يقرأ output/*.json الثمانية (~10-15 ألف عنصر) ويرفعها بدفعات 1000
# الزمن المتوقع: 1–3 دقائق. بعدها تحقق:
#   select section_key, count(*) from items group by 1 order by 1;
# وقارن مع "total" في كل ملف JSON.
```

---

## 8) إزالة D1 نهائيًا (قائمة الحذف الدقيقة — تُنفذ بعد أول رفع ناجح)

| # | الملف/الموضع | الإجراء |
|---|---|---|
| 1 | `.github/workflows/update.yml` | حذف خطوة `setup-node` + خطوة `Push new items to Cloudflare D1` + إضافة خطوة Supabase (البند 7.3) |
| 2 | `d1_push.py` | حذف الملف **أو** إبقاؤه موسومًا `DEPRECATED — replaced by supabase_push.py` لمدة إصدار واحد ثم حذف. **لا تبقِ الاثنان يعملان معًا.** |
| 3 | GitHub Secrets | حذف `CLOUDFLARE_API_TOKEN` و `CLOUDFLARE_ACCOUNT_ID` + إضافة `SUPABASE_URL` و `SUPABASE_SERVICE_ROLE_KEY` |
| 4 | `.wrangler/` (مجلد محلي) + أي `wrangler.toml`/`schema.sql` لـ D1 إن وجدت لاحقًا | حذف من المستودع |
| 5 | `deploy.py` / `SETUP.md` | إزالة أي إشارة لـ D1/wrangler/cron-job.org القديم عند تحريرهما مستقبلًا |
| 6 | Cloudflare Dashboard | حذف قاعدة `faselhd-db` (D1) **بعد أسبوع احتفاظ** كـ rollback window |

---

## 9) خطة التنفيذ (Checklist بترتيب صارم)

- [ ] **P0 — Supabase:** إنشاء المشروع → نسخ `SUPABASE_URL` + `sb_secret_...` (البند 4.3) → لصق DDL البند 5.2 في SQL Editor → تشغيله → التحقق `select * from sections;` (8 صفوف).
- [ ] **P0 — RLS:** تنفيذ سياسات البند 6.4 → اختبار قراءة بمفتاح publishable (200) وكتابة به (403/401 متوقع) وكتابة بالـ secret (نجاح).
- [ ] **P0 — RPC:** إنشاء دالة `search_items` (البند 6.2) → اختبار `rpc` بعربي وإنجليزي وخطأ إملائي.
- [ ] **P1 — السكربت:** كتابة `supabase_push.py` حسب مواصفة 7.2 + إضافة `supabase` لـ `requirements.txt` → تجربة محلية `--full` على نسخة تجريبية → مقارنة counts مع JSON.
- [ ] **P1 — CI:** إضافة السرّين لـ GitHub → استبدال خطوة الـ workflow (7.3) → تشغيل `workflow_dispatch` يدوي → مراقبة السجلات (طلب واحد/قسم للدلتا).
- [ ] **P1 — تحقق الترتيب:** `select slug, ord from items where section_key='movies' order by ord asc limit 5;` يجب أن تطابق أول 5 عناصر في `output/movies.json`.
- [ ] **P2 — إزالة D1:** تنفيذ جدول البند 8 (بعد 3 تشغيلات ناجحة متتالية).
- [ ] **P2 — مراقبة:** تنبيه Telegram موجود يبقى؛ يُنصح بإضافة سطر `supabase rows upserted=N` في `update_log.txt`.

**التراجع (Rollback):** ملفات JSON في Git هي النسخة الكاملة — أي فشل يعني إعادة `supabase_push.py --full`. لا فقدان بيانات ما دامت `output/*.json` سليمة.

---

## 10) التكاليف والحدود والملاحظات التشغيلية (2026)

1. **الطبقة المجانية** تكفي للبدء (500MB قاعدة + 5GB نقل) — الكتالوج الحالي (عشرات آلاف الصفوف النصية) بضعة MB. مراقبة `Database → Usage`.
2. **حدود PostgREST:** حجم الطلب الافتراضي ~ few MB — دفعات 1000 صف نصي (~200-400KB) آمنة. عند تضخم الصور/الأوصاف مستقبلًا اخفض إلى 500.
3. **المفاتيح الجديدة** (`sb_secret_...`) قابلة للتدوير من `Settings → API Keys` دون إيقاف القديم أولًا — دوّر فور أي اشتباه.
4. **لا تضع `SUPABASE_SERVICE_ROLE_KEY` في فرونت/موبايل/مستودع عام** — القراءة العامة عبر `publishable` + RLS فقط.
5. **الفهارس الثلاثة** (ord/trgm/tsv) ترفع زمن الكتابة ~2× — مقبول لدفعات نصف ساعة، ومكسب القراءة أكبر بكثير.

---

## 11) المراجع الرسمية المعتمدة (أحدث الوثائق)

1. `supabase.com/docs/reference/python/upsert` — Bulk upsert + `on_conflict` + `ignore_duplicates`.
2. `supabase.com/docs/reference/python/insert` — Bulk insert.
3. `supabase.com/docs/guides/getting-started/api-keys` — مواقع المفاتيح الجديدة (`sb_publishable_`/`sb_secret_`) وإيقاف `anon`/`service_role` نهاية 2026.
4. `supabase.com/docs/guides/database/full-text-search` — `tsvector` generated + GIN + `websearch_to_tsquery` + الترتيب.
5. `postgrest.org` + `supabase.com/docs/guides/api` — معاملات `on_conflict` و`order` و`or=(...ilike...)`.

---

*نهاية الوثيقة — جاهزة للتنفيذ المباشر. الخطوة التالية المقترحة: لصق DDL البند 5.2 في Supabase SQL Editor، ثم بناء `supabase_push.py` وفق مواصفة 7.2.*
