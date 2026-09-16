-- Utilisation split per machine over the last seven days.
--
-- Answers "what is the fleet actually doing" -- the question a depot manager
-- asks first. Minutes come from the state intervals via the daily rollup, so
-- this is range arithmetic that has already been done, not a scan.
--
-- reporting.as_of() rather than now(): the fleet data ends at a fixed instant,
-- so now() would ask about a window with nothing in it. The function rather
-- than the view of the same name, because a STABLE function can be used as an
-- index qualifier and joining the one-row view cannot -- that difference is a
-- full scan of the rollup versus an index scan over the week asked for.
SELECT d.serial,
       d.site_slug,
       d.machine_type,
       round(sum(d.working_minutes)::numeric / 60, 1) AS working_hours,
       round(sum(d.transit_minutes)::numeric / 60, 1) AS transit_hours,
       round(sum(d.idle_on_minutes)::numeric / 60, 1) AS idle_on_hours,
       round(sum(d.off_minutes)::numeric / 60, 1) AS off_hours,
       round(100.0 * sum(d.working_minutes)::numeric
             / nullif(sum(d.working_minutes + d.transit_minutes
                          + d.idle_on_minutes + d.off_minutes)::numeric, 0), 1) AS working_pct
FROM reporting.machine_day d
WHERE d.day > (reporting.as_of() - interval '7 days')::date
GROUP BY d.serial, d.site_slug, d.machine_type
ORDER BY working_pct DESC, d.serial
