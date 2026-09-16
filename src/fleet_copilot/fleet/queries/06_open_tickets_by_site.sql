-- Open service tickets by site, oldest first.
--
-- "Open" is both open and in_progress: a ticket somebody has started but not
-- finished is still work the site is waiting on. Age is measured against as_of
-- rather than now() so the number means the same thing whenever it is asked.
SELECT t.site_slug,
       count(*) AS open_tickets,
       count(*) FILTER (WHERE t.status = 'in_progress') AS in_progress,
       count(DISTINCT t.serial) AS machines_affected,
       round(max(extract(epoch FROM (reporting.as_of() - t.opened_at)) / 86400)::numeric, 1)
           AS oldest_days,
       round(avg(extract(epoch FROM (reporting.as_of() - t.opened_at)) / 86400)::numeric, 1)
           AS mean_age_days
FROM reporting.service_ticket t
WHERE t.status IN ('open', 'in_progress')
GROUP BY t.site_slug
ORDER BY open_tickets DESC, oldest_days DESC
