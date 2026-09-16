-- Distance travelled per machine over the last seven days.
--
-- Summed from the hourly rollup, where it was computed as great-circle steps
-- between consecutive fixes with power-cycle jumps and GPS jitter already
-- excluded. Kilometres per working hour is the useful column: it separates a
-- machine covering ground from one that ran for hours on the same aisle.
SELECT d.serial,
       d.site_slug,
       d.machine_type,
       round((sum(d.distance_m) / 1000)::numeric, 2) AS distance_km,
       round(sum(d.working_minutes)::numeric / 60, 1) AS working_hours,
       round((sum(d.distance_m) / 1000)::numeric
             / nullif(sum(d.working_minutes)::numeric / 60, 0), 2) AS km_per_working_hour
FROM reporting.machine_day d
WHERE d.day > (reporting.as_of() - interval '7 days')::date
GROUP BY d.serial, d.site_slug, d.machine_type
ORDER BY distance_km DESC
