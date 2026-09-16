-- Days on which a machine's internal temperature passed 55 C.
--
-- Sustained work in a warm building takes a machine to around 50 C, so 55 C is
-- the line above which something is wrong rather than merely busy. Reported per
-- machine and day so a one-off hot afternoon is visibly different from a
-- machine that does it every day.
SELECT d.serial,
       d.site_slug,
       d.machine_type,
       d.day,
       round(d.internal_temp_max_c::numeric, 1) AS internal_temp_max_c,
       round(d.internal_temp_avg_c::numeric, 1) AS internal_temp_avg_c,
       round(d.battery_temp_max_c::numeric, 1) AS battery_temp_max_c,
       round(d.working_minutes::numeric / 60, 1) AS working_hours
FROM reporting.machine_day d
WHERE d.day > (reporting.as_of() - interval '30 days')::date
  AND d.internal_temp_max_c > 55
ORDER BY d.internal_temp_max_c DESC, d.serial, d.day
