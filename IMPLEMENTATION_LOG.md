# سجل التنفيذ الكامل — النظام المزدوج FaselHD + TopCinma
> مستودع `Ahmd3301/faselhd-db` | تاريخ التنفيذ: 2026-09-17 | Commit: `66f67c3`
> هذه الوثيقة توثّق **ما نُفّذ فعلًا** (وليس ما خُطط له) — ملفًا ملفًا، رقمًا رقمًا.

---

## 1) `update.py` — FaselHD بفضاء `fd-`

| التغيير | التفصيل |
|---|---|
| المفاتيح | `movies…tvshows` ← `fd-movies…fd-tvshows` (قائمة `SECTIONS` + خريطة `SITE_PATHS` التي تشطب البادئة لمسار الموقع) |
| الجذر | `output/` ← `output/FaselHD/` + `.delta` تحته، مع وسيط `--root` جديد (يقبل مسارًا نسبيًا أو مطلقًا) |
| `fetch_page()` | يبني URL من `site_path()` — الموقع لا يعرف شيئًا عن البادئة |
| `_run_full_scrape()` | الـ spider يعمل بمفاتيح الموقع الأصلية (لا تعديل في `faselhd_spider.py` ولا `pipelines.py`)، ثم يُنقل ملف `output/<site>.json` الذي تكتبه الـ pipeline إلى `<root>/<fd-key>.json` مع إعادة كتابة حقلي `section` و`section_key` وحذف المؤقت |
| `write_log()` | الكتلة موسومة `[faselhd]` + سطرا `Total new (faselhd):` و`Duration (faselhd):` |
| كتلة الفشل | أي استثناء أثناء الكتابة/الـ commit يُلحق سطر `Run: … [faselhd] FAILED: …` ثم يُعاد رفعه (ليراه التقرير بدل الصمت) |
| `SKIP_GITHUB_PUSH=1` | يتخطى `github_push.py` (تستخدمه الاختبارات فقط) |

## 2) `topcinma_update.py` — جديد (430 سطرًا)

- الأقسام الستة `tc-*` (ثوابت `wp_id`: 3/4/5 REST، 7/8/9 HTML + `cat_slug` المطابق لـ `bot.js` والمفحوص حيًا).
- **العنصر المخزن = name + link فقط** (+ ميكانيكا الخط: `section_key/rank/slug/added_at` — لا `img` ولا metadata ولا سيرفرات).
- `clean_title()` نقل حرفي من `bot.js` (حذف `فيلم/مسلسل/مترجم اون لاين…`).
- أفلام REST: `per_page=100` + `orderby=date&order=desc`، الدلتا بمقارنة `slug` (موحد مع FaselHD — بلا ملفات حالة)، سقف 20 صفحة دلتا / 500 أول مرة، `sleep 2s`.
- مسلسلات HTML (عبر `parsel` الموجود أصلًا): `.Small--Box a` + regex `/series/([^/]+)` + `h3.title`، سقف 100 دلتا / 300 أول مرة، `sleep 1s`.
- دومينات بديلة بالترتيب (`web.topcinemaa.com` ← `topcinema.io` للـ REST، و`topcinemaa.top` ← `topcinema.io` للـ HTML).
- السجل: كتلة `[topcinma]` + سطر `GRAND TOTAL` (يقرأ كتلة `[faselhd]` الأحدث من نفس الملف) + كتلة `FAILED` عند الكوارث.
- نفس واجهة CLI (`--section/--dry-run/--full/--root`) ودعم `SKIP_GITHUB_PUSH`.

## 3) `supabase_push.py` — مجلدان، طلب واحد/دفعة

- `SOURCE_ROOTS = [output/FaselHD, output/TopCinma]` للكامل والدلتا (الجذر الغائب يُتجاوز بصمت).
- `to_row()`: `"img": item.get("img") or ""` (صفوف TopCinma بلا صور — آمن لعمود `NOT NULL`).
- تنظيف `.delta` تحت المجلدين بعد النجاح. بقية المنطق (`ord`، `upsert on_conflict`، التحقق) دون مساس.

## 4) `github_push.py` — وسيط `--root`

- `--root` (نسبي يُحل من `BASE_DIR`) → `git add <root>/<section>.json + update_log.txt`. كل سكربت يودع ملفات جذره (حتى commit-ين/تشغيل كحد أقصى).

## 5) `telegram_notify.py` — إعادة كتابة (التقرير الموحد، `HTML`)

- `parse_blocks()`: يستخرج أحدث كتلة لكل مصدر (ويدعم الكتل القديمة غير الموسومة كـ faselhd).
- القالب: ترويسة `🔄 Auto Update Report #N` + التاريخ + كتلة `🎬 FaselHD` + كتلة `🎞 TopCinma` + `📦 Total` + تذييل 30 دقيقة.
- القواعد: المحدّثة فقط في `<code>` تنازليًا، الأصفار في `skipped:`، `✅ up to date`، `🔴 failed` + `⚠ partial`، إلغاء ازدواج `Total new`/`New items` القديم، `escape_html` للأسماء.

## 6) `.github/workflows/update.yml`

- خطوة `python topcinma_update.py` بعد FaselHD (نفس الأسرار)، و`timeout-minutes: 15` ← `25`. لا أسرار جديدة.

## 7) `tests/test_update.py` — 10/10 ✅

- الجذر المؤقت `output/.test-tmp` (لا تمس البيانات الحقيقية) + `SKIP_GITHUB_PUSH=1` + `fd-series` للموك (و`SITE_SECTION="series"` لمسارات الموك والـ spider والـ admin لأن الموك يعرف مسارات الموقع فقط).
- جديد: `test_9` (ثلاث حالات `clean_title`) + `test_10` (تقرير المصدرين + `Total: +9 new`). النتيجة: `10 passed in 61s`.

## 8) ترحيل ملفات `output/` (نقل git +重写 مفاتيح)

`output/*.json` (8 ملفات) ← `output/FaselHD/fd-*.json` مع `section` و`items[].section_key` الجديدة — **24,570 عنصرًا**: movies 14191، series 4146، anime 1948، asian-series 1490، asian-movies 1337، hindi 883، anime-movies 398، tvshows 177.

## 9) هجرة Supabase (عبر REST API فقط — بلا SQL يدوي إلا المنجز سابقًا)

1. `upsert` صفوف `sections` الـ 14 (8 `fd-` + 6 `tc-`) — نجح.
2. تطهير المفاتيح الثمانية القديمة: 7 أقسام حُذفت بطلب واحد لكل منها، أما `movies` (14,191) فتجاوز مهلة العبارة (`57014`) حتى بدفعات 50 — **السبب**: trigger العدّاد يحسب `count(*)` مع كل صف. الحل: حذف بشرائح `id` (بدون `limit`) × 15 شريحة — نجح.
3. حذف صفوف `sections` القديمة الثمانية.
4. `supabase_push.py --full`: **24,570 صفًا بـ 31 طلبًا** — كل الأعداد مطابقة (`verify`).

## 10) كشط TopCinma الأولي + رفعه

| القسم | العناصر | الزمن |
|---|---|---|
| tc-movies-foreign | 3,299 | ~دقائق (REST) |
| tc-movies-asian | 391 | — |
| tc-anime-movies | 266 | — |
| tc-series-foreign | 1,862 | — |
| tc-series-anime | 886 | 433s (HTML) |
| tc-series-asian | 1,695 | 296s (HTML) |
| **المجموع** | **8,399 (name+link فقط)** | — |

الرفع: وضع الدلتا دفع الستة أقسام (11 طلبًا) — كل الأعداد مطابقة، وملفات `.delta` حُذفت. **الإجمالي في Supabase الآن: 32,969 صفًا.**

## 11) البوت `faselhd-bot` (نُشر — Version `85dbe557`)

- `keyboards.js`: `SOURCES=[fd,tc]` + `FD_SECTIONS` (8) + `TC_SECTIONS` (6) + `buildSourceMenu()` + `buildMainMenu(source)` (افتراضي `fd`) + زر `🔙 المصادر`.
- `handlers.js`: `/start` ← قائمة المصدرين؛ `src:fd/src:tc/src:back`؛ `b` ← المصادر؛ البحث يقبل أي حالة (`toLowerCase`) ويرفض المفاتيح بلا بادئة عارضًا الـ 14 مفتاحًا مجمعة؛ `/stats` مجمعة بالمصدر؛ العنصر بلا `img` ← بطاقة نصية بزر الرابط (مع fallback حذف+إرسال)؛ `/help` بالأمثلة الجديدة.
- `database.js` و`index.js`: **دون تغيير** (تعمل بالمفاتيح كما هي).
- التحقق الحي: `/sections` ← 14 مفتاحًا؛ RPC `mouse` ← `tc-movies-foreign: 3` و`fd-asian-series: 2`.

## 12) الدفع والتحقق الشامل

- Commit `66f67c3` ودفع `main` (نُقلت الملفات الثمانية بنقل git محافظ على التاريخ؛ `bot.js` تُرك خارج الـ commit عمدًا — يحوي توكنًا مكتوبًا).
- تشغيل يدوي `workflow_dispatch` (**#2170**): نجح في 1m32s، دفع دلتا حقيقية (`fd-series +24`)، و`Telegram notification sent OK` بالصيغة الجديدة.

## 13) ملاحظات ختامية

1. دوّر توكن `bot.js` المكشوف من BotFather (سطر 5) ثم انقله لمتغير بيئة.
2. احذف قاعدة D1 (`faselhd-db`) من Cloudflare بعد أسبوع استقرار.
3. أول `cron` تلقائي سيعمل بالمصدرين دون أي تدخل.

---

*نهاية سجل التنفيذ — كل بند أعلاه منفذ ومُتحقق منه.*
