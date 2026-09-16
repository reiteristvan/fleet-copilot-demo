-- Machines reporting positions outside their own site boundary.
--
-- Two counts, because they are different facts. outside_home_samples includes
-- time spent legitimately at the customer's other site; off_site_samples counts
-- only positions no site covers at all. A machine with a large second number is
-- somewhere nobody expects it to be, and that is the one worth a phone call.
--
-- Transit minutes are reported alongside so an excursion explained by a
-- scheduled move between sites can be told apart from one that is not.
SELECT d.serial,
       d.site_slug,
       d.machine_type,
       d.day,
       d.outside_geofence_samples AS outside_home_samples,
       d.off_site_samples,
       d.sample_count,
       round(d.transit_minutes::numeric, 0) AS transit_minutes,
       round(100.0 * d.off_site_samples / nullif(d.sample_count, 0), 1) AS off_site_pct
FROM reporting.machine_day d
WHERE d.day > (reporting.as_of() - interval '90 days')::date
  AND d.off_site_samples > 0
ORDER BY d.off_site_samples DESC, d.serial, d.day
