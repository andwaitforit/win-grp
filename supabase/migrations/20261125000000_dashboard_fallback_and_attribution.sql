-- Three fixes to what reaches the dashboard:
--
-- 1. Fall back to the last version that reconciles. Before, a contract whose
--    newest upload failed reconciliation disappeared entirely, even when an
--    earlier version had parsed cleanly. Now the newest reconciled version is
--    the current one, and the views say when a later revision is in review.
-- 2. Attribute contracts the same way fetch-contracts selects files: by
--    purchaser folder, by alias in the file name, or by the advertiser printed
--    on the order. Before, a file picked up by its name (WNYT's "MA-01"
--    folder, Gray's free-text folders) was stored and then dropped because
--    the view only knew the folder and the "POL/<name>/..." advertiser form.
-- 3. Honest counts: contracts with no parsed contract number each count on
--    their own (they used to share one version group), and files the
--    extractor couldn't read are reported whatever their file name says.

-- Invoices are recognised by content now, so they stop counting as unread.
alter table public.opif_file_extract drop constraint opif_file_extract_outcome_check;
alter table public.opif_file_extract add constraint opif_file_extract_outcome_check
  check (outcome in ('stored', 'unsupported', 'not_pdf', 'invoice'));

drop view public.dashboard_buy_week, public.dashboard_filing, public.dashboard_freshness;
drop view internal.race_contract;

create view internal.race_contract as
select distinct on (a.race_id, c.id)
       a.race_id, a.buyer_key, c.id, c.station, c.contract_number, c.print_date,
       c.contract_start, c.contract_end, c.total_spots, c.gross_cents, c.net_cents,
       c.needs_review, c.parsed_at, c.source_file_id,
       c.version_rank = 1 as is_latest,                         -- newest upload of this contract
       (not c.needs_review and c.class_rank = 1) as is_current, -- newest version that reconciles
       c.has_reconciled,                                        -- some version of this contract reconciles
       c.fcc_created_at, c.download_url
from (
  select c.*, f.purchaser_folder, f.file_name, f.fcc_created_at,
         'https://publicfiles.fcc.gov/api/manager/download/' || f.folder_id || '/' ||
           coalesce(f.file_manager_id, f.file_id) || '.' ||
           coalesce(nullif(f.file_extension, ''), 'pdf') as download_url,
         row_number() over (partition by c.station, c.version_key order by c.version_order) as version_rank,
         row_number() over (partition by c.station, c.version_key, c.needs_review
                            order by c.version_order) as class_rank,
         bool_or(not c.needs_review) over (partition by c.station, c.version_key) as has_reconciled
  from (
    select c.*,
           -- A contract the parser couldn't number is its own version group.
           coalesce(c.contract_number, 'id:' || c.id) as version_key,
           -- Same ordering as opif_contract_current, newest first.
           row_number() over (
             order by coalesce(c.revision, 0) desc,
                      coalesce(c.revision_date, c.original_date) desc nulls last,
                      c.print_date desc nulls last,
                      f.fcc_created_at desc nulls last,
                      c.id desc
           ) as version_order
    from public.opif_contract c
    left join public.opif_file f on f.file_id = c.source_file_id
    where not coalesce(f.is_deleted, false)
  ) c
  left join public.opif_file f on f.file_id = c.source_file_id
) c
join public.race_buyer_alias a
  on lower(a.alias) = lower(c.purchaser_folder)
  or c.advertiser ilike '%/' || a.alias || '/%'   -- Nexstar: POL/<name>/<office>/...
  or c.advertiser ilike a.alias || '%'            -- Gray: the committee name as typed
  or c.file_name ilike '%' || a.alias || '%'      -- the rule fetch-contracts selects files by
order by a.race_id, c.id,
         (lower(a.alias) = lower(c.purchaser_folder)) desc nulls last,
         (c.advertiser ilike '%/' || a.alias || '/%' or c.advertiser ilike a.alias || '%') desc nulls last,
         length(a.alias) desc, a.buyer_key;

-- Weekly planned spots and spend per buyer, station and contract, from the
-- newest version of each contract that reconciles. revision_in_review: a
-- later upload of this contract exists but is held back.
create view public.dashboard_buy_week as
select rc.race_id, rc.buyer_key, l.channel as station, rs.market_key,
       w.week_start, w.week_end,
       sum(w.spots)::int as spots,
       sum(w.spots * w.rate_cents)::bigint as gross_cents,
       rc.contract_number, rc.source_file_id, min(l.source_page) as source_page,
       rc.download_url as source_url, rc.needs_review,
       not rc.is_latest as revision_in_review
from internal.race_contract rc
join public.opif_contract_line l on l.contract_id = rc.id and l.action <> 'D'
join public.opif_contract_week w on w.contract_id = l.contract_id and w.line_no = l.line_no
join public.race_station rs on rs.race_id = rc.race_id and rs.call_sign = l.channel
where rc.is_current
group by rc.race_id, rc.buyer_key, l.channel, rs.market_key, w.week_start, w.week_end,
         rc.contract_number, rc.source_file_id, rc.download_url, rc.needs_review, rc.is_latest;

-- Every reconciled version of each attributed contract, for the "new filings"
-- feed. superseded = a later reconciled version exists.
create view public.dashboard_filing as
select rc.race_id, rc.buyer_key, rc.station, rc.contract_number,
       'order'::text as doc_type,
       coalesce(rc.fcc_created_at::date, rc.print_date) as filed_date,
       rc.contract_start as flight_start, rc.contract_end as flight_end,
       rc.total_spots as spots, rc.gross_cents, rc.net_cents,
       not rc.is_current as superseded, rc.needs_review,
       rc.download_url as source_url,
       (rc.is_current and not rc.is_latest) as revision_in_review
from internal.race_contract rc
join public.race_station rs on rs.race_id = rc.race_id and rs.call_sign = rc.station
where not rc.needs_review;

-- Refresh times and what is not on the dashboard:
--   orders_in_review          contracts with no version that reconciles (missing entirely)
--   orders_on_earlier_version contracts shown from an earlier version; the newest is in review
--   orders_unparsed           files named like orders that no parser reads
--   files_unread              other files no parser reads and that aren't invoices; may hide orders
--   lines_off_station         current order lines printed on a channel the race config doesn't list
create view public.dashboard_freshness as
select r.race_id,
       (select max(s.last_run_at) from public.opif_sync_state s
          join public.station st on st.entity_id = s.entity_id
          join public.race_station rs on rs.call_sign = st.call_sign and rs.race_id = r.race_id) as synced_at,
       (select max(rc.parsed_at) from internal.race_contract rc where rc.race_id = r.race_id) as parsed_at,
       (select count(*) from public.opif_file_extract x
          join public.opif_file f on f.file_id = x.file_id
          join public.race_station rs on rs.call_sign = f.call_sign and rs.race_id = r.race_id
          where x.outcome = 'unsupported' and f.doc_kind = 'order' and not f.is_deleted)::int as orders_unparsed,
       (select count(*) from internal.race_contract rc
          where rc.race_id = r.race_id and rc.is_latest and rc.needs_review
            and not rc.has_reconciled)::int as orders_in_review,
       (select count(*) from internal.race_contract rc
          where rc.race_id = r.race_id and rc.is_current and not rc.is_latest)::int as orders_on_earlier_version,
       (select count(*) from public.opif_file_extract x
          join public.opif_file f on f.file_id = x.file_id
          join public.race_station rs on rs.call_sign = f.call_sign and rs.race_id = r.race_id
          where x.outcome = 'unsupported' and f.doc_kind <> 'order' and not f.is_deleted)::int as files_unread,
       (select count(*) from internal.race_contract rc
          join public.opif_contract_line l on l.contract_id = rc.id and l.action <> 'D'
          where rc.race_id = r.race_id and rc.is_current
            and not exists (select 1 from public.race_station rs
                            where rs.race_id = rc.race_id and rs.call_sign = l.channel))::int as lines_off_station
from (select distinct race_id from public.race_station) r;

revoke all on public.dashboard_buy_week, public.dashboard_filing, public.dashboard_freshness
  from anon, authenticated;
grant select on public.dashboard_buy_week, public.dashboard_filing, public.dashboard_freshness
  to anon, authenticated;
