-- Every write of a preference takes a NEW generation from one sequence, and a generation is never reused: unlike `version` (which counts changes and starts again at 1 when a preference is
-- recreated after a removal), it tells a confirmation read back before a removal and a save of the SAME technician afterwards apart.
create sequence preference_generation;
alter table client_preferences add column generation bigint not null default nextval('preference_generation');
