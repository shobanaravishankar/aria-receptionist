-- A call's memory is saved by compare-and-set: a save only succeeds against the revision it was loaded at, so two invocations of one call (parallel tool calls, on any instance) cannot overwrite
-- each other's counters, identity or attempts. The revision is a column, not part of the document.
alter table call_state add column rev integer not null default 0;
