-- ============================================================================
-- FaselHD catalog - full Supabase schema (schema `public`)
-- Faithful reconstruction of the live production database:
--   24 sections + items (+ poster columns) + users + section counter trigger
--   + search_items RPC + RLS.  Safe to run top-to-bottom on a fresh project.
-- Run in: Supabase Dashboard -> SQL Editor -> New query -> Run.
-- ============================================================================

-- 0) Extensions -------------------------------------------------------------
create extension if not exists pg_trgm;    -- partial / fuzzy search
create extension if not exists unaccent;   -- reserved (not used yet)

-- 1) Sections ---------------------------------------------------------------
create table if not exists public.sections (
  key         text primary key,               -- e.g. fd-movies / tc-series-anime / os-ar26-movies
  name        text not null default '',       -- display only (emoji + label) - no logic
  items_count integer not null default 0,     -- maintained by trigger
  updated_at  timestamptz not null default now()
);

-- 2) Items ------------------------------------------------------------------
create table if not exists public.items (
  id          bigint generated always as identity primary key,
  section_key text not null references public.sections(key) on update cascade,
  slug        text not null,
  name        text not null,
  img         text not null,                  -- original website poster ("" allowed)
  link        text not null,                  -- "" for Ostora items
  added_at    timestamptz not null default now(),
  ord         bigint not null,                -- ordering key: ORDER BY ord ASC (newest = smallest)
  poster_ws   text,                           -- original website poster URL
  poster_cf   text,                           -- Cloudflare Pages hosted poster URL
  search_tsv  tsvector generated always as (
    setweight(to_tsvector('simple', coalesce(name, '')), 'A') ||
    setweight(to_tsvector('simple', coalesce(slug, '')), 'B')
  ) stored,
  constraint items_section_slug_unique unique (section_key, slug),
  constraint items_slug_not_empty check (char_length(slug) > 0)
);

-- 3) Indexes ----------------------------------------------------------------
create index if not exists items_section_ord_idx
  on public.items (section_key, ord asc);
create index if not exists items_name_trgm_idx
  on public.items using gin (name gin_trgm_ops);
create index if not exists items_slug_trgm_idx
  on public.items using gin (slug gin_trgm_ops);
create index if not exists items_search_tsv_idx
  on public.items using gin (search_tsv);
create index if not exists items_poster_cf_null_idx
  on public.items (section_key) where poster_cf is null or poster_cf = '';

-- 4) Users (Telegram bot language prefs) ------------------------------------
create table if not exists public.users (
  chat_id bigint primary key,
  lang    text not null default 'ar'
);

-- 5) Section counter (after every insert/delete) ----------------------------
create or replace function public.refresh_section_count()
returns trigger language plpgsql as $$
begin
  update public.sections
     set items_count = (select count(*) from public.items
                         where section_key = coalesce(new.section_key, old.section_key)),
         updated_at = now()
   where key = coalesce(new.section_key, old.section_key);
  return coalesce(new, old);
end $$;

drop trigger if exists trg_refresh_section_count on public.items;
create trigger trg_refresh_section_count
after insert or delete on public.items
for each row execute function public.refresh_section_count();

-- 6) Search RPC -------------------------------------------------------------
create or replace function public.search_items(
  q text, sec text default null, limit_count int default 25, offset_count int default 0
)
returns table (section_key text, slug text, name text, img text, link text,
               added_at timestamptz, ord bigint, score real)
language sql stable as $$
  with params as (
    select websearch_to_tsquery('simple', q) as tsq, greatest(length(trim(q)), 0) as qlen
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

-- 7) Row Level Security -----------------------------------------------------
alter table public.sections enable row level security;
alter table public.items    enable row level security;
alter table public.users    enable row level security;

drop policy if exists "public read sections" on public.sections;
drop policy if exists "public read items"    on public.items;
create policy "public read sections" on public.sections for select using (true);
create policy "public read items"    on public.items    for select using (true);

grant select on public.sections, public.items to anon, authenticated;

-- 8) Seed the 24 sections (keys + display names) ----------------------------
insert into public.sections (key, name) values
  ('fd-movies',          '🎬 [FD] أفلام'),
  ('fd-series',          '📺 [FD] مسلسلات'),
  ('fd-anime',           '🏯 [FD] أنمي'),
  ('fd-asian-series',    '🌏 [FD] مسلسلات آسيوية'),
  ('fd-asian-movies',    '🎭 [FD] أفلام آسيوية'),
  ('fd-hindi',           '🇮🇳 [FD] هندي'),
  ('fd-anime-movies',    '🎞️ [FD] أفلام أنمي'),
  ('fd-tvshows',         '📡 [FD] برامج تلفزيون'),
  ('tc-movies-foreign',  '🎬 [TC] افلام اجنبي'),
  ('tc-series-foreign',  '📺 [TC] مسلسلات اجنبي'),
  ('tc-series-asian',    '📺 [TC] مسلسلات اسيوية'),
  ('tc-series-anime',    '📺 [TC] مسلسلات انمي'),
  ('tc-anime-movies',    '🎬 [TC] افلام انمي'),
  ('tc-movies-asian',    '🎬 [TC] افلام اسيوي'),
  ('os-ar-series',       '🌙 [OS] مسلسلات عربي'),
  ('os-arall-movies',    '🌙 [OS] افلام عربية'),
  ('os-rn-series',       '🌙 [OS] مسلسلات رمضان'),
  ('os-ar26-movies',     '🌙 [OS] افلام عربية 26'),
  ('os-ar25-movies',     '🌙 [OS] افلام عربية 25'),
  ('os-ar24-movies',     '🌙 [OS] افلام عربية 24'),
  ('os-ar23-movies',     '🌙 [OS] افلام عربية 23'),
  ('os-ar22-movies',     '🌙 [OS] افلام عربية 22'),
  ('os-ar21-movies',     '🌙 [OS] افلام عربية 21'),
  ('os-ar20-movies',     '🌙 [OS] افلام عربية 20')
on conflict (key) do update set name = excluded.name;
