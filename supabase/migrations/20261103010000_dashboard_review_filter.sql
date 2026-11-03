-- Keep contracts that failed reconciliation off the dashboard. They stay in
-- opif_contract with needs_review set, as the review queue; the dashboard only
-- counts them.
--
-- orders_unparsed now counts only files the file-name classifier calls
-- orders: fetch-contracts also tries "other" files, and most of those that no
-- parser reads are invoices or forms, not orders.

create or replace view public.dashboard_buy_week as
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
where rc.is_current and not rc.needs_review
group by rc.race_id, rc.buyer_key, l.channel, rs.market_key, w.week_start, w.week_end,
         rc.contract_number, rc.source_file_id, rc.download_url, rc.needs_review;

create or replace view public.dashboard_filing as
select rc.race_id, rc.buyer_key, rc.station, rc.contract_number,
       'order'::text as doc_type,
       coalesce(rc.fcc_created_at::date, rc.print_date) as filed_date,
       rc.contract_start as flight_start, rc.contract_end as flight_end,
       rc.total_spots as spots, rc.gross_cents, rc.net_cents,
       not rc.is_current as superseded, rc.needs_review,
       rc.download_url as source_url
from internal.race_contract rc
join public.race_station rs on rs.race_id = rc.race_id and rs.call_sign = rc.station
where not rc.needs_review;

create or replace view public.dashboard_freshness as
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
          where rc.race_id = r.race_id and rc.is_current and rc.needs_review)::int as orders_in_review
from (select distinct race_id from public.race_station) r;

revoke all on public.dashboard_buy_week, public.dashboard_filing, public.dashboard_freshness
  from anon, authenticated;
grant select on public.dashboard_buy_week, public.dashboard_filing, public.dashboard_freshness
  to anon, authenticated;
