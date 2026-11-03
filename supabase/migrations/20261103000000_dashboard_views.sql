-- Race reference data, an extraction log, and read-only views for the static
-- dashboard.
--
-- The dashboard reads the dashboard_* views with the publishable (anon) key.
-- They are owner-rights views on purpose: they expose a fixed set of columns
-- derived from public FCC political files and nothing else, while the
-- underlying tables stay authenticated-read / service-role-write.

-- Which OPIF purchasers belong to which race buyer. Seeded from the race
-- config by `wingrp seed-race`. Matching is case-insensitive on the
-- purchaser folder, or on "/<alias>/" inside a contract's advertiser field.
create table public.race_buyer_alias (
  race_id    text not null,
  buyer_key  text not null,
  alias      text not null,
  primary key (race_id, alias)
);

-- Stations (or channels printed on contracts, e.g. EWLP) per race market.
create table public.race_station (
  race_id     text not null,
  call_sign   text not null,
  market_key  text not null,
  primary key (race_id, call_sign)
);

-- One row per OPIF file the extractor has looked at, so the daily job doesn't
-- re-download PDFs in formats it can't parse yet. A newer parser version
-- re-tries them.
create table public.opif_file_extract (
  file_id         uuid primary key references public.opif_file (file_id) on delete cascade,
  pdf_sha256      text,
  outcome         text not null check (outcome in ('stored', 'unsupported', 'not_pdf')),
  parser          text not null,
  parser_version  text not null,
  attempted_at    timestamptz not null default now()
);

alter table public.race_buyer_alias  enable row level security;
alter table public.race_station      enable row level security;
alter table public.opif_file_extract enable row level security;
create policy race_buyer_alias_read  on public.race_buyer_alias  for select to authenticated using (true);
create policy race_station_read      on public.race_station      for select to authenticated using (true);
create policy opif_file_extract_read on public.opif_file_extract for select to authenticated using (true);

-- Contracts attributed to a race buyer, with version ranking and download
-- link inlined. Lives in a schema the API doesn't expose and runs with owner
-- rights, so the dashboard_* views below can read it for anon without going
-- through the caller-rights opif_contract_current / opif_file_link views.
create schema internal;
revoke all on schema internal from public, anon, authenticated;

create view internal.race_contract as
select distinct on (a.race_id, c.id)
       a.race_id, a.buyer_key, c.id, c.station, c.contract_number, c.print_date,
       c.contract_start, c.contract_end, c.total_spots, c.gross_cents, c.net_cents,
       c.needs_review, c.parsed_at, c.source_file_id, c.version_rank = 1 as is_current,
       c.fcc_created_at, c.download_url
from (
  select c.*, f.purchaser_folder, f.fcc_created_at,
         'https://publicfiles.fcc.gov/api/manager/download/' || f.folder_id || '/' ||
           coalesce(f.file_manager_id, f.file_id) || '.' ||
           coalesce(nullif(f.file_extension, ''), 'pdf') as download_url,
         row_number() over (  -- same ordering as opif_contract_current
           partition by c.station, c.contract_number
           order by coalesce(c.revision, 0) desc,
                    coalesce(c.revision_date, c.original_date) desc nulls last,
                    c.print_date desc nulls last,
                    f.fcc_created_at desc nulls last,
                    c.id desc
         ) as version_rank
  from public.opif_contract c
  left join public.opif_file f on f.file_id = c.source_file_id
  where not coalesce(f.is_deleted, false)
) c
join public.race_buyer_alias a
  on lower(a.alias) = lower(c.purchaser_folder)
  or c.advertiser ilike '%/' || a.alias || '/%'
order by a.race_id, c.id, a.buyer_key;

-- Weekly planned spots and spend per buyer, station and contract.
create view public.dashboard_buy_week as
select rc.race_id, rc.buyer_key, l.channel as station, rs.market_key,
       w.week_start, w.week_end,
       sum(w.spots)::int as spots,
       sum(w.spots * w.rate_cents)::bigint as gross_cents,
       rc.contract_number, rc.source_file_id, min(l.source_page) as source_page,
       rc.download_url as source_url, rc.needs_review
from internal.race_contract rc
join public.opif_contract_line l on l.contract_id = rc.id and l.action <> 'D'
join public.opif_contract_week w on w.contract_id = l.contract_id and w.line_no = l.line_no
join public.race_station rs on rs.race_id = rc.race_id and rs.call_sign = l.channel
where rc.is_current
group by rc.race_id, rc.buyer_key, l.channel, rs.market_key, w.week_start, w.week_end,
         rc.contract_number, rc.source_file_id, rc.download_url, rc.needs_review;

-- Every uploaded version of each attributed contract, newest first, for the
-- "new filings" feed. superseded = a later version of the same contract exists.
create view public.dashboard_filing as
select rc.race_id, rc.buyer_key, rc.station, rc.contract_number,
       'order'::text as doc_type,
       coalesce(rc.fcc_created_at::date, rc.print_date) as filed_date,
       rc.contract_start as flight_start, rc.contract_end as flight_end,
       rc.total_spots as spots, rc.gross_cents, rc.net_cents,
       not rc.is_current as superseded, rc.needs_review,
       rc.download_url as source_url
from internal.race_contract rc
join public.race_station rs on rs.race_id = rc.race_id and rs.call_sign = rc.station;

-- When the data was last refreshed, and how many race order files are still
-- in formats the extractor can't parse.
create view public.dashboard_freshness as
select r.race_id,
       (select max(s.last_run_at) from public.opif_sync_state s
          join public.station st on st.entity_id = s.entity_id
          join public.race_station rs on rs.call_sign = st.call_sign and rs.race_id = r.race_id) as synced_at,
       (select max(rc.parsed_at) from internal.race_contract rc where rc.race_id = r.race_id) as parsed_at,
       (select count(*) from public.opif_file_extract x
          join public.opif_file f on f.file_id = x.file_id
          join public.race_station rs on rs.call_sign = f.call_sign and rs.race_id = r.race_id
          where x.outcome = 'unsupported' and not f.is_deleted)::int as orders_unparsed
from (select distinct race_id from public.race_station) r;

revoke all on public.dashboard_buy_week, public.dashboard_filing, public.dashboard_freshness
  from anon, authenticated;
grant select on public.dashboard_buy_week, public.dashboard_filing, public.dashboard_freshness
  to anon, authenticated;
