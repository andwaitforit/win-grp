-- Shared public FCC OPIF data: stations, political-file documents, and the
-- raw change-feed events they are derived from. These tables hold public
-- data only (no org-scoped rows). Authenticated users can read them; only the
-- service role (ingestion workers) writes.

create table public.station (
  entity_id      text primary key,               -- OPIF facility / entity id
  call_sign      text not null,
  service_code   text not null,                  -- TV, FM, AM, CABLE, ...
  dma            text,                           -- Nielsen DMA name as reported by FCC
  network        text,
  community_city text,
  community_state text,
  licensee       text,
  raw            jsonb not null,                 -- facility record as returned by FCC
  updated_at     timestamptz not null default now()
);
create unique index station_call_sign_service_idx on public.station (call_sign, service_code);
create index station_dma_idx on public.station (dma);

-- Append-only log of /api/manager/file/history rows. Idempotent on
-- (file_id, history_status, event_ts).
create table public.opif_file_event (
  id              bigint generated always as identity primary key,
  file_id         uuid not null,
  history_status  text not null,                 -- new | delete | purge | ...
  event_ts        timestamptz not null,          -- FCC last_update_ts of the event
  entity_id       text not null,
  raw             jsonb not null,
  ingested_at     timestamptz not null default now(),
  unique (file_id, history_status, event_ts)
);
create index opif_file_event_file_idx on public.opif_file_event (file_id, event_ts);
create index opif_file_event_entity_idx on public.opif_file_event (entity_id, event_ts);

-- Current state of each OPIF file, derived from its events.
create table public.opif_file (
  file_id          uuid primary key,
  entity_id        text not null,                -- not FK: the feed can include stations we haven't loaded
  call_sign        text,
  service_code     text,
  folder_id        uuid not null,
  folder_path      text not null,
  file_name        text not null,
  file_extension   text,
  file_size        bigint,
  fcc_file_status  text,
  fcc_created_at   timestamptz,
  fcc_updated_at   timestamptz,
  is_deleted       boolean not null default false,
  deleted_at       timestamptz,

  -- Parsed from folder_path: Political Files/{year}/{category}/...
  pol_year         int,
  pol_category     text,                         -- Federal | State | Local | Non-Candidate Issue Ads
  pol_office       text,
  purchaser_folder text,

  -- Parsed from file_name (best effort; filename conventions vary by traffic system)
  doc_kind         text not null default 'other' check (doc_kind in ('nab', 'order', 'invoice', 'other')),
  is_revision      boolean not null default false,
  contract_number  text,
  flight_start     date,
  flight_end       date,

  -- PDF content. FCC blocks automated download, so this is filled by user upload
  -- (or a sanctioned FCC route if one is granted).
  pdf_sha256       text,
  pdf_storage_path text,
  pdf_source       text check (pdf_source in ('user_upload', 'fcc')),
  pdf_attached_at  timestamptz,

  first_seen_at    timestamptz not null default now(),
  updated_at       timestamptz not null default now()
);
create index opif_file_entity_idx on public.opif_file (entity_id, fcc_created_at desc);
create index opif_file_political_idx on public.opif_file (pol_year, pol_category, pol_office, purchaser_folder)
  where pol_year is not null;

-- Per-station sync cursor for the change feed.
create table public.opif_sync_state (
  entity_id         text primary key,
  synced_through    date not null,               -- inclusive; next run starts here (overlap is safe)
  last_run_at       timestamptz not null default now(),
  last_event_count  int not null default 0
);

-- Download link for the human-driven fetch step (opens in a normal browser).
create view public.opif_file_link
with (security_invoker = true) as
select f.*,
       'https://publicfiles.fcc.gov/api/manager/download/' || f.folder_id || '/' || f.file_id || '.' ||
         coalesce(nullif(f.file_extension, ''), 'pdf') as download_url
from public.opif_file f;

-- RLS: shared public data. Read for signed-in users; writes via service role only.
alter table public.station          enable row level security;
alter table public.opif_file_event  enable row level security;
alter table public.opif_file        enable row level security;
alter table public.opif_sync_state  enable row level security;

create policy station_read         on public.station         for select to authenticated using (true);
create policy opif_file_event_read on public.opif_file_event for select to authenticated using (true);
create policy opif_file_read       on public.opif_file       for select to authenticated using (true);
-- opif_sync_state: no policies (worker-only).
