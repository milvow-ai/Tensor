-- Tensor: initial schema (Postgres 15+; tested on 16, Supabase runs 17).
--
-- Everything lives in the `tensor` schema, not `public`, so Supabase's REST API
-- does not expose it unless you add it to the exposed schemas on purpose.
-- RLS is enabled on every table anyway as defence in depth; the worker connects
-- as the table owner and is unaffected.
--
-- Design notes are in docs/02-architecture.md ("Data model").

begin;

create schema if not exists tensor;
create extension if not exists citext;

set local search_path = tensor, public;

-- ---------------------------------------------------------------------------
-- helpers
-- ---------------------------------------------------------------------------

create or replace function tensor.touch_updated_at() returns trigger
language plpgsql as $$
begin
  new.updated_at := now();
  return new;
end $$;

-- sha256 of a lower-cased, trimmed value. Used so suppression and contact
-- history survive deletion of the underlying personal data.
create or replace function tensor.pii_hash(v text) returns text
language sql immutable as $$
  select encode(sha256(convert_to(lower(btrim(v)), 'UTF8')), 'hex')
$$;

-- ---------------------------------------------------------------------------
-- configuration snapshots
-- ---------------------------------------------------------------------------

-- A playbook is one offer aimed at one ICP (see docs/04-playbooks.md).
-- The YAML file in config/playbooks is the source of truth; every sync stores
-- a validated snapshot here so every lead can be traced to the exact config
-- that qualified and messaged it.
create table tensor.playbooks (
  id           text primary key,                 -- slug, e.g. 'hiring-signal-ops'
  version      int  not null,
  status       text not null check (status in ('draft','active','paused','archived')),
  config       jsonb not null,
  config_hash  text not null,
  updated_at   timestamptz not null default now()
);

create table tensor.playbook_versions (
  playbook_id  text not null references tensor.playbooks(id) on delete cascade,
  version      int  not null,
  config       jsonb not null,
  config_hash  text not null,
  created_at   timestamptz not null default now(),
  primary key (playbook_id, version)
);

create table tensor.inboxes (
  id          text primary key,                  -- from config/tensor.yaml
  address     citext not null unique,
  provider    text not null,                     -- 'zoho' | 'gmail' | 'workspace' | 'smtp'
  daily_cap   int  not null check (daily_cap >= 0),
  status      text not null default 'active'
              check (status in ('warming','active','paused','burned')),
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- companies and what we know about them (playbook-agnostic)
-- ---------------------------------------------------------------------------

create table tensor.companies (
  id              uuid primary key default gen_random_uuid(),
  domain          citext not null unique,        -- registrable domain, the dedupe key
  name            text,
  website         text,
  country         char(2),                       -- ISO 3166-1 alpha-2
  region          text,
  city            text,
  timezone        text,                          -- IANA, used for send windows
  naics           text,                          -- NAICS 2022 code, as precise as known
  industry_label  text,
  employee_count  int,
  employee_band   text check (employee_band in ('1-9','10-49','50-199','200-999','1000+')),
  phone           text,
  linkedin_url    text,
  tech            jsonb not null default '[]',   -- detected technologies
  status          text not null default 'discovered'
                  check (status in ('discovered','enriched','unreachable','error')),
  attempts        int  not null default 0,
  last_error      text,
  enriched_at     timestamptz,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);
create index companies_status_idx on tensor.companies (status) where status = 'discovered';

-- Where each company came from. One company can be found by several sources.
create table tensor.company_sources (
  company_id  uuid not null references tensor.companies(id) on delete cascade,
  source      text not null,                     -- adapter id, e.g. 'osm', 'ats_jobs', 'csv'
  source_ref  text not null default '',          -- external id or URL
  payload     jsonb,
  first_seen  timestamptz not null default now(),
  primary key (company_id, source, source_ref)
);

-- Pages fetched during enrichment. Only an excerpt is stored to stay inside
-- the free-tier database size; the full markdown lives in the worker's disk cache.
create table tensor.pages (
  id            bigint generated always as identity primary key,
  company_id    uuid not null references tensor.companies(id) on delete cascade,
  url           text not null,
  http_status   int,
  content_hash  text,
  excerpt       text check (length(excerpt) <= 4000),
  fetched_at    timestamptz not null default now(),
  unique (company_id, url)
);

-- Grounding facts. Every personalised sentence in an email must cite at least
-- one of these by id (enforced by the writer's validator, see docs/05).
create table tensor.facts (
  id           bigint generated always as identity primary key,
  company_id   uuid not null references tensor.companies(id) on delete cascade,
  kind         text not null,                    -- 'service','location','team_member','hiring',
                                                 -- 'tech','review','news','cta','process_evidence',...
  statement    text not null,                    -- normalised claim in plain English
  quote        text,                             -- verbatim supporting text
  source_url   text,
  extractor    text not null,                    -- 'rule:<name>' or 'llm:<model>'
  confidence   real check (confidence between 0 and 1),
  observed_at  timestamptz not null default now(),
  expires_at   timestamptz,                      -- e.g. job posts go stale
  unique (company_id, kind, statement)
);
create index facts_company_idx on tensor.facts (company_id);

-- ---------------------------------------------------------------------------
-- playbook-specific: qualification and segmentation
-- ---------------------------------------------------------------------------

-- A target is a company being evaluated by one playbook.
create table tensor.targets (
  id             uuid primary key default gen_random_uuid(),
  playbook_id    text not null references tensor.playbooks(id),
  company_id     uuid not null references tensor.companies(id) on delete cascade,
  status         text not null default 'new'
                 check (status in ('new','qualified','borderline','disqualified',
                                   'no_contact','ready','enrolled','closed')),
  score          numeric(5,2),
  gate_results   jsonb,     -- {gate_id: {pass: bool, detail}}
  signal_results jsonb,     -- {signal_id: {hit: bool, points, fact_ids}}
  rubric         jsonb,     -- [{criterion, score, fact_ids, note}]
  verdict_reason text,
  judged_by      text,      -- 'rules', 'llm:<model>', 'human:<name>'
  segment_key    text,
  persona        text,
  priority       int not null default 0,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  unique (playbook_id, company_id)
);
create index targets_queue_idx on tensor.targets (playbook_id, status, priority desc);

-- ---------------------------------------------------------------------------
-- people
-- ---------------------------------------------------------------------------

create table tensor.contacts (
  id                uuid primary key default gen_random_uuid(),
  company_id        uuid not null references tensor.companies(id) on delete cascade,
  full_name         text,
  first_name        text,
  last_name         text,
  title             text,
  persona           text,                        -- persona id from the playbook
  email             citext unique,
  email_status      text not null default 'unknown'
                    check (email_status in ('unknown','valid','accept_all','risky','invalid')),
  email_source      text,                        -- 'site','pattern','hunter','clay','manual',...
  email_verified_at timestamptz,
  is_role_account   boolean not null default false,   -- info@, sales@ ...
  linkedin_url      text,
  phone             text,
  country           char(2),
  timezone          text,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);
create index contacts_company_idx on tensor.contacts (company_id);

-- ---------------------------------------------------------------------------
-- outreach
-- ---------------------------------------------------------------------------

create table tensor.enrollments (
  id              uuid primary key default gen_random_uuid(),
  target_id       uuid not null references tensor.targets(id) on delete cascade,
  contact_id      uuid not null references tensor.contacts(id) on delete cascade,
  company_id      uuid not null references tensor.companies(id) on delete cascade,
  playbook_id     text not null references tensor.playbooks(id),
  inbox_id        text references tensor.inboxes(id),
  segment_key     text,
  angle           text,
  status          text not null default 'drafting'
                  check (status in ('drafting','pending_approval','approved','active','paused',
                                    'replied','completed','bounced','unsubscribed','failed','cancelled')),
  current_step    int  not null default 0,
  next_action_at  timestamptz,
  thread_key      text,                          -- RFC Message-ID of step 1, for threading
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  unique (contact_id, playbook_id)
);
-- Deliberate policy: one live conversation per company at a time, across all
-- playbooks. Prevents two playbooks emailing the same business in one week.
create unique index enrollments_one_live_per_company
  on tensor.enrollments (company_id)
  where status in ('drafting','pending_approval','approved','active','paused');
create index enrollments_due_idx on tensor.enrollments (next_action_at) where status = 'active';

create table tensor.messages (
  id                        uuid primary key default gen_random_uuid(),
  enrollment_id             uuid references tensor.enrollments(id) on delete cascade,
  direction                 text not null check (direction in ('out','in')),
  step                      int,
  inbox_id                  text references tensor.inboxes(id),
  rfc_message_id            text unique,
  in_reply_to               text,
  from_address              citext,
  to_address                citext,
  subject                   text,
  body_text                 text not null,
  status                    text not null
                            check (status in ('draft','pending_approval','approved','scheduled',
                                              'sent','failed','rejected','received')),
  scheduled_for             timestamptz,
  sent_at                   timestamptz,
  received_at               timestamptz,
  -- outbound: {template, angle, fact_ids[], prompt_id, model, lint{}, critic{}}
  generation                jsonb,
  -- inbound: interested | question | not_now | not_interested | referral |
  --          out_of_office | unsubscribe | bounce | auto_reply | other
  classification            text,
  classification_confidence real,
  handled_at                timestamptz,
  error                     text,
  created_at                timestamptz not null default now()
);
create index messages_queue_idx on tensor.messages (status, scheduled_for)
  where status in ('pending_approval','approved','scheduled');
create index messages_inbound_idx on tensor.messages (classification, handled_at)
  where direction = 'in';

-- Work a human does: LinkedIn touches, calls, reviews. Lets Tensor plan
-- channels it is not allowed to automate.
create table tensor.manual_tasks (
  id             bigint generated always as identity primary key,
  enrollment_id  uuid references tensor.enrollments(id) on delete cascade,
  kind           text not null check (kind in ('linkedin_connect','linkedin_message','call',
                                               'review_draft','reply','other')),
  due_at         timestamptz not null default now(),
  payload        jsonb,
  status         text not null default 'open' check (status in ('open','done','skipped')),
  done_at        timestamptz
);

-- ---------------------------------------------------------------------------
-- compliance memory (survives PII purges)
-- ---------------------------------------------------------------------------

create table tensor.suppressions (
  id          bigint generated always as identity primary key,
  kind        text not null check (kind in ('email','domain')),
  value_hash  text not null,                     -- tensor.pii_hash(value)
  reason      text not null check (reason in ('unsubscribe','bounce','complaint','not_interested',
                                               'manual','customer','competitor','legal')),
  created_at  timestamptz not null default now(),
  unique (kind, value_hash)
);

-- Who we have emailed and when, by hash, so cool-down rules still work after
-- names and addresses of non-responders are deleted.
create table tensor.contact_ledger (
  value_hash         text primary key,           -- tensor.pii_hash(email)
  domain_hash        text not null,              -- tensor.pii_hash(domain)
  first_contacted_at timestamptz not null,
  last_contacted_at  timestamptz not null,
  times_enrolled     int not null default 1
);
create index contact_ledger_domain_idx on tensor.contact_ledger (domain_hash);

create or replace function tensor.is_suppressed(p_email text) returns boolean
language sql stable as $$
  select exists (
    select 1 from tensor.suppressions s
    where (s.kind = 'email'  and s.value_hash = tensor.pii_hash(p_email))
       or (s.kind = 'domain' and s.value_hash = tensor.pii_hash(split_part(p_email, '@', 2)))
  )
$$;

-- ---------------------------------------------------------------------------
-- budget meter: free-tier quotas are a first-class resource
-- ---------------------------------------------------------------------------

create table tensor.quota_usage (
  provider      text not null,                   -- 'gemini','groq','hunter','clay','firecrawl',...
  metric        text not null,                   -- 'requests','credits','searches','verifications'
  period        text not null check (period in ('day','month')),
  period_start  date not null,
  used          int  not null default 0,
  limit_value   int  not null,
  primary key (provider, metric, period, period_start)
);

-- Atomically reserve n units. Returns false (and reserves nothing) when the
-- reservation would exceed the limit, so callers fall back to the next provider.
create or replace function tensor.consume_quota(
  p_provider text, p_metric text, p_period text, p_n int, p_limit int
) returns boolean
language plpgsql as $$
declare
  v_start date := case p_period
                    when 'day'   then (now() at time zone 'utc')::date
                    when 'month' then date_trunc('month', now() at time zone 'utc')::date
                  end;
  v_ok boolean;
begin
  if v_start is null then
    raise exception 'unknown period %', p_period;
  end if;
  insert into tensor.quota_usage as q (provider, metric, period, period_start, used, limit_value)
  values (p_provider, p_metric, p_period, v_start, 0, p_limit)
  on conflict (provider, metric, period, period_start) do update set limit_value = excluded.limit_value;

  update tensor.quota_usage
     set used = used + p_n
   where provider = p_provider and metric = p_metric and period = p_period
     and period_start = v_start and used + p_n <= limit_value
  returning true into v_ok;

  return coalesce(v_ok, false);
end $$;

-- ---------------------------------------------------------------------------
-- observability and long-term memory
-- ---------------------------------------------------------------------------

create table tensor.runs (
  id           bigint generated always as identity primary key,
  stage        text not null,                    -- 'discover','enrich','qualify','contacts',
                                                 -- 'draft','send','inbox','digest','learn'
  playbook_id  text,
  started_at   timestamptz not null default now(),
  finished_at  timestamptz,
  processed    int not null default 0,
  succeeded    int not null default 0,
  failed       int not null default 0,
  notes        jsonb
);

create table tensor.llm_calls (
  id             bigint generated always as identity primary key,
  task           text not null,                  -- task id from config/tensor.yaml models.tasks
  provider       text not null,
  model          text not null,
  prompt_id      text,                           -- prompts/<file>@<sha>
  input_tokens   int,
  output_tokens  int,
  latency_ms     int,
  ok             boolean not null,
  error          text,
  est_cost_usd   numeric(10,6) not null default 0,
  created_at     timestamptz not null default now()
);
create index llm_calls_task_day_idx on tensor.llm_calls (task, created_at);

-- Append-only log of everything that happened. The audit trail, and the raw
-- material for the weekly review.
create table tensor.events (
  id         bigint generated always as identity primary key,
  at         timestamptz not null default now(),
  actor      text not null,                      -- 'worker:<stage>', 'claude', 'human:<name>'
  entity     text not null,                      -- 'company','target','contact','enrollment','message',...
  entity_id  text not null,
  type       text not null,
  data       jsonb
);
create index events_entity_idx on tensor.events (entity, entity_id);

-- What the system has learned. Claude proposes, a human accepts; accepted
-- learnings are fed back into the writer and qualifier prompts.
create table tensor.learnings (
  id           bigint generated always as identity primary key,
  playbook_id  text references tensor.playbooks(id),
  scope        text not null,                    -- 'qualification','messaging','sourcing','deliverability'
  insight      text not null,
  evidence     jsonb,                            -- counts, message ids, reply ids
  status       text not null default 'proposed'
               check (status in ('proposed','accepted','rejected','superseded')),
  created_by   text not null,
  created_at   timestamptz not null default now(),
  decided_at   timestamptz
);

-- ---------------------------------------------------------------------------
-- triggers
-- ---------------------------------------------------------------------------

create trigger companies_touch   before update on tensor.companies   for each row execute function tensor.touch_updated_at();
create trigger targets_touch     before update on tensor.targets     for each row execute function tensor.touch_updated_at();
create trigger contacts_touch    before update on tensor.contacts    for each row execute function tensor.touch_updated_at();
create trigger enrollments_touch before update on tensor.enrollments for each row execute function tensor.touch_updated_at();
create trigger inboxes_touch     before update on tensor.inboxes     for each row execute function tensor.touch_updated_at();

-- ---------------------------------------------------------------------------
-- views: what a human (or Claude over MCP) looks at
-- ---------------------------------------------------------------------------

create view tensor.v_funnel as
select
  p.id as playbook_id,
  count(*) filter (where t.status is not null)                       as targets,
  count(*) filter (where t.status in ('qualified','ready','enrolled','closed')) as qualified,
  count(*) filter (where t.status = 'disqualified')                  as disqualified,
  count(*) filter (where t.status = 'borderline')                    as borderline,
  count(*) filter (where t.status in ('ready','enrolled','closed'))  as with_contact,
  count(*) filter (where t.status in ('enrolled','closed'))          as enrolled
from tensor.playbooks p
left join tensor.targets t on t.playbook_id = p.id
group by p.id;

-- Reply performance per playbook x segment x angle, counted per contact
-- (not per message) so follow-ups do not inflate the denominator.
create view tensor.v_reply_rates as
with first_touch as (
  select e.id, e.playbook_id, e.segment_key, e.angle
  from tensor.enrollments e
  where exists (select 1 from tensor.messages m
                where m.enrollment_id = e.id and m.direction = 'out' and m.status = 'sent')
),
replies as (
  select m.enrollment_id,
         bool_or(m.classification in ('interested','question','referral')) as positive,
         bool_or(m.classification not in ('out_of_office','auto_reply','bounce'))  as human_reply
  from tensor.messages m
  where m.direction = 'in'
  group by m.enrollment_id
)
select f.playbook_id, f.segment_key, f.angle,
       count(*)                                         as contacted,
       count(*) filter (where r.human_reply)            as replied,
       count(*) filter (where r.positive)               as positive,
       round(100.0 * count(*) filter (where r.human_reply) / nullif(count(*),0), 1) as reply_pct,
       round(100.0 * count(*) filter (where r.positive)    / nullif(count(*),0), 1) as positive_pct
from first_touch f
left join replies r on r.enrollment_id = f.id
group by f.playbook_id, f.segment_key, f.angle;

create view tensor.v_approval_queue as
select m.id as message_id, e.playbook_id, e.segment_key, e.angle, m.step,
       c.name as company, c.domain, ct.full_name, ct.title, m.to_address,
       m.subject, m.body_text, m.generation -> 'critic' as critic, m.created_at
from tensor.messages m
join tensor.enrollments e on e.id = m.enrollment_id
join tensor.companies  c  on c.id = e.company_id
join tensor.contacts   ct on ct.id = e.contact_id
where m.status = 'pending_approval'
order by m.created_at;

create view tensor.v_hot_replies as
select m.id as message_id, m.received_at, m.classification, m.classification_confidence,
       c.name as company, c.domain, ct.full_name, ct.title, m.from_address,
       m.subject, m.body_text, e.playbook_id, e.angle
from tensor.messages m
join tensor.enrollments e on e.id = m.enrollment_id
join tensor.companies  c  on c.id = e.company_id
join tensor.contacts   ct on ct.id = e.contact_id
where m.direction = 'in'
  and m.classification in ('interested','question','referral')
  and m.handled_at is null
order by m.received_at desc;

create view tensor.v_inbox_today as
select i.id as inbox_id, i.address, i.daily_cap, i.status,
       count(m.id) filter (where m.sent_at >= date_trunc('day', now())) as sent_today
from tensor.inboxes i
left join tensor.messages m on m.inbox_id = i.id and m.direction = 'out' and m.status = 'sent'
group by i.id;

-- ---------------------------------------------------------------------------
-- row level security (defence in depth; no policies = no API access)
-- ---------------------------------------------------------------------------

do $$
declare r record;
begin
  for r in select tablename from pg_tables where schemaname = 'tensor' loop
    execute format('alter table tensor.%I enable row level security', r.tablename);
  end loop;
end $$;

commit;
