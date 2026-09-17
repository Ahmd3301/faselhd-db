# مواصفة التكامل المزدوج: FaselHD + TopCinma — البادئتان `fd-` و`tc-`
> مستودع `Ahmd3301/faselhd-db` | الإصدار 2.0 (يستبدل 1.0) | 2026-09-17
> أمر صريح: **لا تعديل كودي الآن** — هذه الوثيقة تحديث مواصفات فقط، والتنفيذ لاحقًا.
> المنهجية: قراءة الملفات البرمجية فقط (`*.py` + `*.js` + `*.yml`)، مع فحص حي سابق لـ WordPress REST API.

---

## 0) تأكيد الفهم (نعم — هذا بالضبط ما سينفذ لاحقًا)

1. ✅ كشط الموقعين **كل نصف ساعة** (`cron */30` واحد يشغلهما بالتتابع) وتحديث البيانات.
2. ✅ فضاء أسماء موحد: أقسام FaselHD الثمانية ببادئة **`fd-`**، وأقسام TopCinma الستة ببادئة **`tc-`**.
3. ✅ مجلدا `Output`: `FaselHD/` و`TopCinma` (بهذا التهجئة والأحرف الكبيرة حرفيًا)، وبداخل كل منهما ملفات JSON الخاصة به.
4. ✅ تحديث Supabase يشمل TopCinma (نفس الدفعات وحيدة الطلب، نفس عقد `ord`).
5. ✅ `/start` تعرض رسالة بزرين: **FaselHD** و**TopCinma**، ثم أقسام المصدر المختار.
6. ✅ البحث بالصيغة: `/search fd-asian-series mouse` و`/search tc-asian-series mouse`.
7. ✅ شكل الرسالة المرسلة موضح حرفيًا في البند 6 (القالب + مثال حي + شاشة `/start`).

---

## 1) فضاء الأسماء Canonical (ملزم — أحرف صغيرة دائمًا في التخزين)

طلبك كتب `fd-Asian-series` بحرف كبير، والمعتمد: **التخزين والمفاتيح أحرف صغيرة دائمًا** (`fd-asian-series`)، والبوت **يقبل أي حالة** (يحوّل المدخل إلى lowercase قبل المطابقة). لا استثناءات.

### 1.1 أقسام FaselHD — إعادة تسمية من المفاتيح الحالية

| المفتاح الحالي (يُهجر) | المفتاح الجديد (المعتمد) |
|---|---|
| `movies` | `fd-movies` |
| `series` | `fd-series` |
| `anime` | `fd-anime` |
| `asian-series` | `fd-asian-series` |
| `asian-movies` | `fd-asian-movies` |
| `hindi` | `fd-hindi` |
| `anime-movies` | `fd-anime-movies` |
| `tvshows` | `fd-tvshows` |

### 1.2 أقسام TopCinma — جديدة كليًا (IDs مثبتة من `bot.js` والفحص الحي)

| المفتاح الجديد | WP cat ID | المصدر | الحجم الحي التقريبي |
|---|---|---|---|
| `tc-movies-foreign` | 3 | REST أفلام أجنبي | ~3,300 |
| `tc-movies-asian` | 4 | REST أفلام آسيوي | ~390 |
| `tc-anime-movies` | 5 | REST أفلام أنمي | ~270 |
| `tc-series-foreign` | 7 | HTML مسلسلات أجنبي (`/series/`) | آلاف المسلسلات (46K حلقة — **لا تُكشط الحلقات**) |
| `tc-series-anime` | 8 | HTML مسلسلات أنمي | آلاف (22K حلقة) |
| `tc-series-asian` | 9 | HTML مسلسلات آسيوية | آلاف (33K حلقة) |

المجموع: **14 قسمًا** (8 + 6)، كل مفتاح فريد عالميًا — لا تصادم ممكن في `UNIQUE(section_key, slug)`.

---

## 2) شجرة `Output` (بهذا التهجئة حرفيًا — حساسة لحالة الأحرف على Ubuntu)

```text
output/
  FaselHD/
    fd-movies.json  fd-series.json  fd-anime.json  fd-asian-series.json
    fd-asian-movies.json  fd-hindi.json  fd-anime-movies.json  fd-tvshows.json
  TopCinma/
    tc-movies-foreign.json  tc-movies-asian.json  tc-anime-movies.json
    tc-series-foreign.json  tc-series-anime.json  tc-series-asian.json
  .delta/
    FaselHD/<section>.json
    TopCinma/<section>.json
```

⚠️ تنبيه: الأحرف الكبيرة (`F`, `H`, `T`, `C`) ملزمة في كل مرجع كودي (`OUTPUT_DIR`، `github_push`، `supabase_push`، الاختبارات) — أي اختلاف حالة حرف يكسر التشغيل على Linux بينما يعمل على Windows. يُفحص ذلك باختبار CI صريح.

---

## 3) Supabase — التحديث + هجرة إعادة التسمية (مرة واحدة)

### 3.1 SQL الهجرة (يُنفذ قبل أول رفع — يحذف المفاتيح القديمة اليتيمة)

```sql
-- 1) حذف صفوف المفاتيح القديمة (بدون بادئة) حتى لا تبقى يتيمة
delete from public.items
 where section_key in ('movies','series','anime','asian-series',
                       'asian-movies','hindi','anime-movies','tvshows');
delete from public.sections
 where key in ('movies','series','anime','asian-series',
               'asian-movies','hindi','anime-movies','tvshows');

-- 2) صفوف المصدرين بالنظام الجديد (14 قسمًا)
insert into public.sections (key, name) values
  ('fd-movies','🎬 [FD] أفلام'), ('fd-series','📺 [FD] مسلسلات'),
  ('fd-anime','🏯 [FD] أنمي'), ('fd-asian-series','🌏 [FD] مسلسلات آسيوية'),
  ('fd-asian-movies','🎭 [FD] أفلام آسيوية'), ('fd-hindi','🇮🇳 [FD] هندي'),
  ('fd-anime-movies','🎞️ [FD] أفلام أنمي'), ('fd-tvshows','📡 [FD] برامج تلفزيون'),
  ('tc-movies-foreign','🎬 [TC] افلام اجنبي'), ('tc-movies-asian','🎬 [TC] افلام اسيوي'),
  ('tc-anime-movies','🎬 [TC] افلام انمي'), ('tc-series-foreign','📺 [TC] مسلسلات اجنبي'),
  ('tc-series-anime','📺 [TC] مسلسلات انمي'), ('tc-series-asian','📺 [TC] مسلسلات اسيوية')
on conflict (key) do update set name = excluded.name;
```

### 3.2 بعد الـ SQL: تعبئة كاملة واحدة

`python supabase_push.py --full` (يمسح المجلدين) — ~40K صف بدفعات 1000/طلب. ثم كل تشغيل دوري دلتا فقط.

### 3.3 ما لا يتغير في السكيمة

الجداول والفهارس والـ Trigger ودالة `search_items()` **كما هي** — المفاتيح الجديدة مجرد قيم في `section_key`.

---

## 4) الكشط كل نصف ساعة (تشغيل واحد، مصدران بالتتابع)

```yaml
# .github/workflows/update.yml — cron: '*/30 * * * *', timeout-minutes: 25
steps:
  - python update.py --root output/FaselHD          # 8 أقسام fd- (نفس الخوارزمية)
  - python topcinma_update.py --root output/TopCinma # 6 أقسام tc- (REST + HTML)
  - python supabase_push.py                          # دلتا المجلدين معًا
  - python telegram_notify.py                        # التقرير الموحد (البند 6)
```

- `topcinma_update.py`: أقسام REST (`per_page=100`, التوقف عند `id <= last_seen_id`، حد 20 صفحة) + أقسام HTML (مقارنة `slug` على `/category/{slug}/page/{n}/`، حد 100 صفحة) + `cleanTitle()` من `bot.js` + `sleep 2s` + `retries=5`.
- `update_log.txt` v2: كتلة `[faselhd]` ثم كتلة `[topcinma]` ثم سطر `GRAND TOTAL` (الصيغة في 6.3).

---

## 5) البوت — `/start` ببوابة مصدرين + البحث بالبادئة

### 5.1 شاشة `/start` (العرض الحرفي — `parse_mode=HTML`)

```html
🎬 <b>مرحبًا بك — اختر المصدر</b>

اختر المنصة لتصفح أقسامها:
```

```
┌──────────────┬──────────────┐
│ 🎬 FaselHD   │ 🎞 TopCinma  │
└──────────────┴──────────────┘
```

- `callback_data`: `src:fd` و`src:tc` (جديدان، لا يكسران الأزرار القديمة).
- الضغط يحرر نفس الرسالة إلى شبكة أقسام المصدر (8 أو 6 أزرار بنفس مكون القائمة الحالي) + زر `🔙 المصادر` (`callback_data: src:back`) للعودة.
- باقي التدفق (صفحات `ord`، عرض العنصر، اللغة) **دون تغيير** — يعمل بأي `section_key`.

### 5.2 البحث — الصيغة الملزمة والأمثلة

```text
/search <section-key> <query…>      (query ≥ 3 أحرف بعد التنظيف)
/search fd-asian-series mouse       → يبحث في FaselHD فقط
/search tc-asian-series mouse       → يبحث في TopCinma فقط
```

- المطابقة **غير حساسة للحالة**: `FD-Asian-Series` ≡ `fd-asian-series` (يُطبّع بـ lowercase).
- قسم بلا بادئة (`/search movies x`) → مرفوض برسالة: `❌ استخدم مفتاحًا ببادئة: fd-… أو tc-…` + قائمة المفاتيح الـ 14.
- مفتاح خاطئ البادئة (`/search xx-movies y`) → `❌ القسم "xx-movies" غير موجود.` + المفاتيح الصالحة.
- حد Telegram لأزرار الـ callback (64 بايت) سليم: أطول مفتاح `fd-anime-movies` (15) + بادئات التنقل الحالية « الحد.

---

## 6) شكل الرسالة المرسلة (المواصفة الملزمة — HTML)

### 6.1 القواعد

1. رقم واحد لكل مصدر + إجمالي واحد — لا ازدواج (`Total new`/`New items` السابقة ملغاة).
2. الأقسام المحدّثة فقط في كتلة `<code>` مرتبة تنازليًا؛ الأصفار في سطر `skipped:` واحد.
3. مصدر بلا جديد: `✅ up to date` بدون كتلة. مصدر فاشل: `🔴 failed — السبب` والإجمالي `⚠ partial`.
4. الحجم ≈ 700 حرف « حد 4096.

### 6.2 القالب الرسمي

```html
<b>🔄 Auto Update Report #2169</b>
🗓 Thursday, September 17, 2026, 02:46:56 PM
<b>━━━━━━━━━━━━━━</b>
🎬 <b>FaselHD</b> — 🆕 +10 new · ⏱ 48s
<code>fd-series        +4 new
fd-movies        +2 new
fd-asian-series  +2 new
fd-anime         +1 new
fd-tvshows       +1 new</code>
<i>skipped: fd-asian-movies, fd-hindi, fd-anime-movies</i>
<b>━━━━━━━━━━━━━━</b>
🎞 <b>TopCinma</b> — 🆕 +6 new · ⏱ 41s
<code>tc-movies-foreign  +3 new
tc-series-anime    +2 new
tc-anime-movies    +1 new</code>
<i>skipped: tc-movies-asian, tc-series-foreign, tc-series-asian</i>
<b>━━━━━━━━━━━━━━</b>
📦 <b>Total: +16 new</b> · FaselHD 10 + TopCinma 6 · ⏱ 89s
⏳ Next report in 30 minutes.
```

### 6.3 صيغة `update_log.txt` v2 (مصدر القالب أعلاه)

```text
Run: 2026-09-17T14:46:56Z [faselhd]
[fd-movies        ] +2 -> new total
[fd-series        ] +4 -> new total
[fd-anime         ] +1 -> new total
[fd-asian-series  ] +2 -> new total
[fd-asian-movies  ] +0 -> skipped
[fd-hindi         ] +0 -> skipped
[fd-anime-movies  ] +0 -> skipped
[fd-tvshows       ] +1 -> new total
Total new (faselhd): 10 items across 5 sections
Duration (faselhd): 48s

Run: 2026-09-17T14:47:50Z [topcinma]
[tc-movies-foreign] +3 -> new total
[tc-movies-asian  ] +0 -> skipped
[tc-anime-movies  ] +1 -> new total
[tc-series-foreign] +0 -> skipped
[tc-series-anime  ] +2 -> new total
[tc-series-asian  ] +0 -> skipped
Total new (topcinma): 6 items across 3 sections
Duration (topcinma): 41s

GRAND TOTAL: 16 new items | Faselhd 10 (48s) + TopCinma 6 (41s)
```

---

## 7) Checklist التنفيذ (لاحقًا — مرتب صارم)

- [ ] **P0**: SQL الهجرة (3.1) في SQL Editor ← تحقق `select key from sections` = 14 صفًا.
- [ ] **P0**: نقل `output/*.json` إلى `output/FaselHD/fd-*.json` (نقل git حرفي للمحتوى) + إنشاء `output/TopCinma/`.
- [ ] **P0**: `update.py` (مفاتيح `fd-` + `--root`) + `topcinma_update.py` (مفاتيح `tc-`) + سجل v2.
- [ ] **P0**: `supabase_push.py` يمسح المجلدين + `--full` أولي + مطابقة الأعداد والترتيب.
- [ ] **P1**: `telegram_notify.py` على قالب 6.2 (`up to date`/`failed`/`partial`).
- [ ] **P1**: workflow (`timeout 25` + الخطوتان) + `workflow_dispatch` تجريبي + مقارنة `x-wp-total`.
- [ ] **P1**: البوت: `src:fd/src:tc/src:back` + شبكتا الأقسام + بحث لايقبل إلا مفتاحًا ببادئة (case-insensitive).
- [ ] **P2**: تحديث الاختبارات (`fd-tvshows` بدل `tvshows` + حالة الأحرف) + تدوير توكن `bot.js` المكشوف.

**القبول:** أعداد Supabase = `total` كل JSON؛ `ord` تصاعدي والأحدث أولًا في 14 قسمًا؛ الرسالة تطابق 6.2 حرفيًا في الحالات الثلاث؛ فشل مصدر لا يكسر الآخر ولا ينتج تقريرًا كاذبًا.

---

*نهاية الوثيقة v2.0 — بانتظار أمر التنفيذ.*
