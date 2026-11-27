-- Auto-apply incident, 2026-11-26
-- Cause: onboarding silently set auto_apply_settings.is_active = true, and the
-- daily cap only counted 'queued'/'submitted' rows, so every 2-5 min tick
-- queued up to daily_cap more applications. Deactivated users were never excluded.
-- Run section 1 first (read-only). Take a backup before section 2.

-- ── 1. DIAGNOSE (read-only) ────────────────────────────────────────────────
-- 1a. How many users have auto-apply switched on, and how many are deactivated?
SELECT COUNT(*)                                             AS settings_rows,
       SUM(s.is_active)                                     AS aa_on,
       SUM(s.is_active AND s.paused_at IS NULL)             AS aa_running,
       SUM(s.is_active AND s.paused_at IS NULL AND u.is_active = 0) AS running_for_deactivated_users,
       SUM(s.fully_automatic)                               AS fully_automatic
FROM auto_apply_settings s JOIN users u ON u.id = s.user_id;

-- 1b. Worst-affected users
SELECT u.id, u.email, u.is_active AS user_active, s.is_active AS aa_on, s.paused_at,
       s.daily_cap, s.fully_automatic,
       COUNT(j.id)        AS auto_apps,
       MAX(j.created_at)  AS last_auto_app
FROM auto_apply_settings s
JOIN users u ON u.id = s.user_id
LEFT JOIN job_applications j ON j.user_id = u.id AND j.auto_apply_stage IS NOT NULL
GROUP BY u.id ORDER BY auto_apps DESC LIMIT 50;

-- 1c. Stage breakdown — how many were actually SUBMITTED to employers
SELECT auto_apply_stage, COUNT(*) FROM job_applications
WHERE auto_apply_stage IS NOT NULL GROUP BY 1;

-- 1d. Submitted to employers, per user (these are the ones users need to be told about)
SELECT user_id, COUNT(*) FROM job_applications
WHERE auto_apply_stage = 'submitted' GROUP BY 1 ORDER BY 2 DESC;

-- ── 2. STOP IT (writes) ────────────────────────────────────────────────────
-- Onboarding enabled this for everyone, so we can't tell real opt-ins apart.
-- Turn it off for all users; they re-enable from Settings.
UPDATE auto_apply_settings SET is_active = 0, paused_at = UTC_TIMESTAMP()
WHERE is_active = 1;

-- Cancel anything still in flight so the dispatcher doesn't pick it up.
UPDATE job_applications SET auto_apply_stage = 'cancelled'
WHERE auto_apply_stage IN ('queued', 'preparing');

-- ── 3. OPTIONAL CLEAN-UP (decide first — not reversible without the backup) ──
-- Unreviewed auto-drafts that were never sent. Inspect before deleting:
-- SELECT user_id, COUNT(*) FROM job_applications
--   WHERE auto_apply_stage IN ('awaiting_review','failed','cancelled') GROUP BY 1;
-- DELETE FROM job_applications
--   WHERE auto_apply_stage IN ('awaiting_review','failed','cancelled') AND user_id = <id>;
