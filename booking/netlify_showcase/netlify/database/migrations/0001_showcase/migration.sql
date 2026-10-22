-- The Serenity Spa showcase: clients, appointments, the owner's alert rows, preferred technicians, and what the voice tools remember within a call.
-- Same data model as the Python demo's SQLite file (aria_booking/appointments/store.py, preferences.py), in Postgres. Times are epoch SECONDS (bigint) exactly as in the SQLite file, so a
-- snapshot of the synthetic demo data can be imported unchanged. No extension is needed.
--
-- Overlap is refused by the DATABASE, not by the application: each confirmed appointment carries two generated ranges, and an exclusion constraint on each refuses a second confirmed
-- appointment whose range overlaps. The staff range is the technician's whole OCCUPIED time (cleanup before and after included) and is keyed by the technician (id * 10^8 + minutes since the
-- epoch), so ranges of different technicians can never overlap; the client range is the SERVICE time keyed the same way by client. Touching ranges do not overlap. A reschedule that overlaps
-- its OWN old time is one UPDATE and is allowed. These constraints cover every stored row, however it got there (booked, seeded, imported).

create table clients (
  client_id   bigserial primary key,
  name        text    not null,
  name_key    text    not null,
  phone       text    not null,
  created_at  bigint  not null,
  unique (phone, name_key)
);
create index clients_phone on clients (phone);

create table appointments (
  appt_id               bigserial primary key,
  reference             text    not null unique,
  client_id             bigint  not null references clients (client_id),
  service_id            text    not null,
  service_name          text    not null,
  category              text    not null,
  staff_id              text    not null check (staff_id ~ '^[0-9]{1,12}$'),
  start_utc             bigint  not null check (start_utc % 60 = 0),
  duration_minutes      integer not null check (duration_minutes > 0),
  buffer_before_minutes integer not null default 0 check (buffer_before_minutes >= 0),
  buffer_after_minutes  integer not null default 0 check (buffer_after_minutes >= 0),
  status                text    not null check (status in ('confirmed', 'cancelled', 'completed', 'no_show')),
  version               integer not null default 1,
  origin                text    not null,
  seed_key              text    unique,
  created_at            bigint  not null,
  updated_at            bigint  not null,
  cancelled_at          bigint,
  cancel_reason         text,
  staff_rng  int8range generated always as (
    int8range(staff_id::bigint * 100000000 + (start_utc - buffer_before_minutes * 60) / 60,
              staff_id::bigint * 100000000 + (start_utc + (duration_minutes + buffer_after_minutes) * 60) / 60)
  ) stored,
  client_rng int8range generated always as (
    int8range(client_id * 100000000 + start_utc / 60,
              client_id * 100000000 + (start_utc + duration_minutes * 60) / 60)
  ) stored,
  constraint no_double_booked_staff  exclude using gist (staff_rng  with &&) where (status = 'confirmed'),
  constraint no_double_booked_client exclude using gist (client_rng with &&) where (status = 'confirmed')
);
create index appointments_staff_time  on appointments (staff_id, start_utc);
create index appointments_client_time on appointments (client_id, start_utc);
create index appointments_time        on appointments (start_utc);

create table appointment_events (
  event_id  bigserial primary key,
  reference text   not null,
  kind      text   not null,
  detail    jsonb  not null,
  at        bigint not null
);
create index appointment_events_reference on appointment_events (reference);

-- idempotency keys: a repeated confirmed request returns the first result
create table requests (
  request_key text   primary key,
  kind        text   not null,
  reference   text   not null,
  created_at  bigint not null
);

-- the owner's prototype alerts: rows only, written with the change they report; this showcase never sends one
create table outbox (
  outbox_id  bigserial primary key,
  dedupe_key text   not null unique,
  kind       text   not null,
  reference  text   not null,
  body       text   not null,
  status     text   not null check (status in ('pending', 'sending', 'sent', 'failed', 'uncertain')),
  attempts   integer not null default 0,
  last_error text,
  created_at bigint not null,
  claimed_at bigint,
  sent_at    bigint
);

-- a returning client's preferred technician for one service family (demo only)
create table client_preferences (
  client_id  bigint  not null references clients (client_id),
  family_id  text    not null,
  staff_id   text    not null,
  version    integer not null default 1,
  created_at bigint  not null,
  updated_at bigint  not null,
  primary key (client_id, family_id)
);
create table preference_requests (
  request_key text   primary key,
  kind        text   not null,
  client_id   bigint not null,
  family_id   text   not null,
  created_at  bigint not null
);
create table preference_events (
  event_id  bigserial primary key,
  client_id bigint not null,
  family_id text   not null,
  kind      text   not null,
  detail    jsonb  not null,
  at        bigint not null
);

-- what the voice tools remember WITHIN one call (a Function keeps no memory between invocations): numbers tried (as digests), failed matches, changes made, who the call settled on
create table call_state (
  call_id    text   primary key,
  doc        jsonb  not null,
  updated_at bigint not null
);
-- confirmation tokens: bound to the call and to exactly what was read back; a newer read-back of the same kind replaces the call's older one; they expire
create table pending_confirmations (
  token      text   primary key,
  kind       text   not null,
  call_id    text   not null,
  payload    jsonb  not null,
  expires_at bigint not null
);
create index pending_confirmations_call on pending_confirmations (call_id, kind);

-- small facts: the spa day the lazy upkeep last ran for
create table meta (
  key   text primary key,
  value text not null
);
