-- Poster hosting columns for Cloudflare Pages (imagecdn project).
-- Run once in Supabase Dashboard -> SQL Editor for project
--   rydujeodnaaeachbngdc
--
--   poster_ws : original website poster URL (source of truth)
--   poster_cf : hosted copy on https://imagecdn-agu.pages.dev/...

alter table public.items add column if not exists poster_ws text;
alter table public.items add column if not exists poster_cf text;

-- Optional indexes for lookups / "missing hosted poster" queries.
create index if not exists items_poster_cf_null_idx
    on public.items (section_key)
    where poster_cf is null or poster_cf = '';
