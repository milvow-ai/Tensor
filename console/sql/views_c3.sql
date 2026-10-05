-- =====================================================================================================================
-- Farm Console C3 views (Alembic migration wraps this file verbatim).
--
-- Views supporting Routing, Memory & Evidence, and Control screens:
--   * v_routes: Capability -> ordered pools with enabled status, strategy, remaining capacity and health.
--   * v_facts: Entity facts with source account, confidence, observed/expires dates, and freshness state.
--   * v_evidence: Captured evidence artifacts with sha256, url, thumbnail, captured_at, robots decision, facts count.
-- =====================================================================================================================

-- ---------------------------------------------------------------------------------------------------------------------
-- v_routes: capability -> ordered pools with enabled, strategy, and remaining calls.
-- ---------------------------------------------------------------------------------------------------------------------
create or replace view v_routes with (security_invoker = true) as
select
  cap.name as capability,
  cap.kind as capability_kind,
  cap.description as capability_description,
  cap.default_strategy,
  cap.cache_ttl_seconds,
  cr.position,
  cr.enabled,
  cr.provider_id,
  p.name as provider_name,
  p.kind as provider_kind,
  p.executor as provider_executor,
  coalesce(cr.enabled and po.enabled, false) as is_active,
  po.health,
  po.accounts_usable,
  po.accounts_total,
  po.remaining_calls,
  po.unlimited
from capabilities cap
join capability_routes cr on cr.capability = cap.name
join providers p on p.id = cr.provider_id
left join v_pool_overview po on po.provider_id = cr.provider_id
order by cap.name, cr.position asc;


-- ---------------------------------------------------------------------------------------------------------------------
-- v_facts: entity, attribute, value, source, observed/expires, and computed freshness state.
-- ---------------------------------------------------------------------------------------------------------------------
create or replace view v_facts with (security_invoker = true) as
select
  f.id,
  f.entity_id,
  e.kind as entity_kind,
  e.canonical_key as entity_canonical_key,
  e.name as entity_name,
  f.attribute,
  f.value,
  f.source_connection_id,
  coalesce(c.label, f.source_connection_id) as source_label,
  c.provider_id as source_provider_id,
  f.observed_at,
  f.expires_at,
  f.confidence,
  f.evidence_ids,
  case
    when f.expires_at is not null and f.expires_at < now() then 'expired'
    when f.observed_at < now() - interval '30 days' then 'stale'
    else 'fresh'
  end as freshness_state
from facts f
join entities e on e.id = f.entity_id
left join connections c on c.id = f.source_connection_id
order by f.observed_at desc;


-- ---------------------------------------------------------------------------------------------------------------------
-- v_evidence: captured evidence records with sha256, paths, dates, and referencing facts.
-- ---------------------------------------------------------------------------------------------------------------------
create or replace view v_evidence with (security_invoker = true) as
select
  ev.id,
  ev.sha256,
  ev.path,
  ev.url,
  ev.thumb_path,
  ev.captured_at,
  ev.tool_version,
  'allowed'::text as robots_decision,
  coalesce(fc.facts_count, 0)::int as facts_count
from evidence ev
left join lateral (
  select count(*)::int as facts_count
  from facts f
  where ev.id = any(f.evidence_ids)
) fc on true
order by ev.captured_at desc;


-- ---------------------------------------------------------------------------------------------------------------------
-- Expand farm_commands.kind CHECK constraint for C3 commands (wrapped into migration at merge)
-- ---------------------------------------------------------------------------------------------------------------------
alter table public.farm_commands drop constraint if exists farm_commands_kind_check;
alter table public.farm_commands add constraint farm_commands_kind_check check (kind in (
  'pause','resume','set_priority','set_strategy','set_budget','add_connection','update_connection',
  'remove_connection','set_route','test_connection','ack_alert',
  'cancel_ai_job','set_max_parallel','set_mcp_tool_access','sync_mcp_tools'
));

