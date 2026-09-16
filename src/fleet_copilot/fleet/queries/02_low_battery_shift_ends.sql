-- Machines that finished a day below 20% state of charge more than twice in
-- the last seven days.
--
-- Repeatedly finishing flat is what wears a pack out, and it is a scheduling
-- problem rather than a fault: the machine is being asked to cover more than
-- one charge will carry. Days with no telemetry are excluded -- a machine that
-- never ran did not finish flat.
SELECT d.serial,
       d.site_slug,
       d.machine_type,
       d.battery_chemistry,
       count(*) AS days_below_20_pct,
       round(min(d.battery_soc_end_pct)::numeric, 1) AS lowest_end_pct,
       round(avg(d.battery_soc_end_pct)::numeric, 1) AS mean_end_pct
FROM reporting.machine_day d
WHERE d.day > (reporting.as_of() - interval '7 days')::date
  AND d.sample_count > 0
  AND d.battery_soc_end_pct < 20
GROUP BY d.serial, d.site_slug, d.machine_type, d.battery_chemistry
HAVING count(*) > 2
ORDER BY days_below_20_pct DESC, lowest_end_pct
