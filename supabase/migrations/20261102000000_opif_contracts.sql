-- Structured contracts (orders) extracted from political-file PDFs.
-- Shared public data, like the OPIF tables: authenticated users read, only the
-- service role (workers) writes. Extraction is re-runnable: a contract row is
-- keyed on the PDF's content hash, and re-parsing replaces its lines.

-- The download endpoint wants the file's file_manager_id; file_id returns 400.
-- The change feed carries it in raw; keep it as a column.
alter table public.opif_file add column file_manager_id uuid;
update public.opif_file f
   set file_manager_id = (e.raw ->> 'file_manager_id')::uuid
  from (select distinct on (file_id) file_id, raw
          from public.opif_file_event order by file_id, event_ts desc) e
 where e.file_id = f.file_id and e.raw ? 'file_manager_id';

drop view public.opif_file_link;
create view public.opif_file_link
with (security_invoker = true) as
select f.*,
       'https://publicfiles.fcc.gov/api/manager/download/' || f.folder_id || '/' ||
         coalesce(f.file_manager_id, f.file_id) || '.' ||
         coalesce(nullif(f.file_extension, ''), 'pdf') as download_url
from public.opif_file f;

create table public.opif_contract (
  id                bigint generated always as identity primary key,
  source_pdf_sha256 text not null unique,           -- content hash of the parsed PDF
  source_file_id    uuid references public.opif_file (file_id) on delete set null,
  source            text not null check (source in ('fcc', 'user_upload')),
  parser            text not null,                  -- e.g. wideorbit_contract
  parser_version    text not null,
  raw_text          text not null,                  -- extracted text, so re-parsing needs no PDF

  station           text,                           -- call sign printed on the contract (WWLP, EWLP, ...)
  contract_number   text,
  revision          int,
  alt_order         text,
  advertiser        text,
  product           text,
  estimate          text,
  demographic       text,
  bill_to           text[] not null default '{}',   -- agency/payer name, then address lines
  contract_start    date,
  contract_end      date,
  print_date        date,
  original_date     date,
  revision_date     date,
  periods           jsonb not null default '[]',    -- broadcast-month subtotals as printed

  total_spots       int,
  gross_cents       bigint,
  commission_cents  bigint,
  net_cents         bigint,
  impressions_000   numeric,                        -- station's impressions estimate, when printed (thousands)
  issues            text[] not null default '{}',   -- reconciliation failures
  needs_review      boolean generated always as (cardinality(issues) > 0) stored,

  parsed_at         timestamptz not null default now()
);
create index opif_contract_number_idx on public.opif_contract (station, contract_number);
create index opif_contract_source_file_idx on public.opif_contract (source_file_id);

create table public.opif_contract_line (
  contract_id   bigint not null references public.opif_contract (id) on delete cascade,
  line_no       int not null,
  action        text not null check (action in ('N', 'E', 'D', 'U')),   -- U: unchanged on a revision
  channel       text not null,
  start_date    date not null,
  end_date      date not null,
  description   text not null,
  time_text     text,
  days          text,
  length_s      int,
  spot_type     text,
  spots         int not null,
  amount_cents  bigint not null,
  source_page   int not null,                       -- 1-based PDF page
  primary key (contract_id, line_no)
);

create table public.opif_contract_week (
  contract_id   bigint not null,
  line_no       int not null,
  week_start    date not null,                      -- broadcast week, Monday
  week_end      date not null,
  weekdays      text not null,                      -- Mon..Sun, letters or per-day spot counts
  spots         int not null,
  rate_cents    bigint not null,
  imp_000       numeric,                            -- impressions per spot (thousands), when printed
  primary key (contract_id, line_no, week_start),
  foreign key (contract_id, line_no) references public.opif_contract_line on delete cascade
);

-- One version per (station, contract #): highest revision, then latest
-- revision/print date, then latest upload. Drops contracts whose OPIF file
-- was deleted. Stations re-upload changed contracts ("Updated_..."), so
-- summing opif_contract directly would double-count.
create view public.opif_contract_current
with (security_invoker = true) as
select c.*
from (
  select c.*,
         row_number() over (
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
where c.version_rank = 1;

-- Weekly planned spend from current contracts: the grain GRP estimation uses.
-- Net is gross scaled by the contract's net/gross ratio (agency commission).
create view public.opif_contract_week_current
with (security_invoker = true) as
select c.id as contract_id, c.station, c.contract_number, c.advertiser,
       c.source_file_id, c.needs_review,
       l.line_no, l.channel, l.description, l.time_text, l.source_page,
       w.week_start, w.week_end, w.spots, w.rate_cents,
       w.spots * w.rate_cents as gross_cents,
       w.spots * w.imp_000 as impressions_000,
       case when c.gross_cents > 0
            then round(w.spots * w.rate_cents * c.net_cents::numeric / c.gross_cents)::bigint
       end as net_cents
from public.opif_contract_current c
join public.opif_contract_line l on l.contract_id = c.id and l.action <> 'D'
join public.opif_contract_week w on w.contract_id = l.contract_id and w.line_no = l.line_no;

alter table public.opif_contract      enable row level security;
alter table public.opif_contract_line enable row level security;
alter table public.opif_contract_week enable row level security;

create policy opif_contract_read      on public.opif_contract      for select to authenticated using (true);
create policy opif_contract_line_read on public.opif_contract_line for select to authenticated using (true);
create policy opif_contract_week_read on public.opif_contract_week for select to authenticated using (true);
