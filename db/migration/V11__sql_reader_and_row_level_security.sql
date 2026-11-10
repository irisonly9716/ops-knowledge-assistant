-- ============================================================================
--  V11 -- the role generated SQL runs as, and row-level security
--
--  ops owns the tables, is a superuser and bypasses row-level security: a query run as ops keeps to the asker's
--  department only because the code filters it (app/sql_agent/guard.py). Generated SQL runs as sql_reader instead,
--  and the database holds the department too:
--    - sql_reader can read the five metric views and nothing else: no table, so not ip_address either
--    - row-level security on the six tables behind the views keeps the rows of the department set for the
--      transaction (app.department), or every department's when app.all_departments is 'on' (executives and
--      admins). Nothing set, no rows. The two layers read the same setting, so either holds if the other has a bug
--    - set_config is revoked: it is a function, so a SELECT could call it and change the department halfway through
--      the query. SET LOCAL still sets it: a statement of its own, which can never be part of a SELECT
--
--  The views now belong to sql_views and read their tables as it, replacing V9's security_invoker. With
--  security_invoker a view reads its tables as whoever queries it, and a role granted only the views is refused the
--  tables. Without it a view reads them as its owner, so the owner must not be ops, which bypasses row-level
--  security. sql_views can't log in, owns no table and doesn't bypass row-level security, so the policies hold
--  through the views. They read the settings of the asker's transaction, whichever role reads the tables.
--
--  Roles belong to the whole cluster, not to one database, and the tests apply every migration to databases of
--  their own: a role is created only when missing, and sql_reader's login and password are never set here
--  (scripts/enable_sql_reader.py). ops still sees every row: row-level security doesn't apply to a table's owner.
-- ============================================================================

DO $$
BEGIN
    CREATE ROLE sql_views NOLOGIN;
EXCEPTION WHEN duplicate_object THEN NULL;
END
$$;

DO $$
BEGIN
    CREATE ROLE sql_reader NOLOGIN;
EXCEPTION WHEN duplicate_object THEN NULL;
END
$$;

-- Whatever they were before. sql_reader's LOGIN is left as it is: the script sets it, and a test database must not
-- undo it
ALTER ROLE sql_views NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION;
ALTER ROLE sql_reader NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION;

-- What the code sets for every query anyway (app/sql_agent/execute.py), so a connection that forgets still can't
-- write or run long
ALTER ROLE sql_reader SET default_transaction_read_only = on;
ALTER ROLE sql_reader SET statement_timeout = '5s';

-- ----------------------------------------------------------------------------
--  sql_views: owns the views, reads the tables behind them
-- ----------------------------------------------------------------------------
GRANT USAGE ON SCHEMA public TO sql_views, sql_reader;
GRANT SELECT ON departments, hosts, campaigns, campaign_metrics, campaign_products TO sql_views;
-- Every column but ip_address: a view that selected it would be refused
GRANT SELECT (id, campaign_id, customer_ref, region, dwell_time_minutes, products_purchased, purchase_amount)
    ON campaign_customer_behavior TO sql_views;

ALTER VIEW campaign_summary OWNER TO sql_views;
ALTER VIEW campaign_daily_metrics OWNER TO sql_views;
ALTER VIEW campaign_product_sales OWNER TO sql_views;
ALTER VIEW campaign_customers OWNER TO sql_views;
ALTER VIEW host_profiles OWNER TO sql_views;

ALTER VIEW campaign_summary RESET (security_invoker);
ALTER VIEW campaign_daily_metrics RESET (security_invoker);
ALTER VIEW campaign_product_sales RESET (security_invoker);
ALTER VIEW campaign_customers RESET (security_invoker);
ALTER VIEW host_profiles RESET (security_invoker);

-- ----------------------------------------------------------------------------
--  sql_reader: the views only
-- ----------------------------------------------------------------------------
GRANT SELECT ON campaign_summary, campaign_daily_metrics, campaign_product_sales, campaign_customers, host_profiles
    TO sql_reader;

-- No role here but ops (a superuser, which keeps it) has a use for it
REVOKE EXECUTE ON FUNCTION set_config(text, text, boolean) FROM PUBLIC;

-- ----------------------------------------------------------------------------
--  Row-level security. The department is decided once, on departments; every other table keeps the rows of the
--  departments or campaigns its reader can see, so a table added under campaigns needs the same one-line policy, not
--  the department rule again.
--  current_setting(..., true) is NULL when the setting doesn't exist, and a setting a connection had in an earlier
--  transaction stays defined there as ''. Neither is a department's name or 'on', so both give no rows.
--  Only SELECT has a policy: anything else is refused to every role the policies apply to.
-- ----------------------------------------------------------------------------
ALTER TABLE departments ENABLE ROW LEVEL SECURITY;
ALTER TABLE hosts ENABLE ROW LEVEL SECURITY;
ALTER TABLE campaigns ENABLE ROW LEVEL SECURITY;
ALTER TABLE campaign_metrics ENABLE ROW LEVEL SECURITY;
ALTER TABLE campaign_products ENABLE ROW LEVEL SECURITY;
ALTER TABLE campaign_customer_behavior ENABLE ROW LEVEL SECURITY;

CREATE POLICY asker_can_see ON departments FOR SELECT
    USING (current_setting('app.all_departments', true) = 'on' OR name = current_setting('app.department', true));
CREATE POLICY asker_can_see ON hosts FOR SELECT
    USING (department_id IN (SELECT department_id FROM departments));
CREATE POLICY asker_can_see ON campaigns FOR SELECT
    USING (department_id IN (SELECT department_id FROM departments));
CREATE POLICY asker_can_see ON campaign_metrics FOR SELECT
    USING (campaign_id IN (SELECT campaign_id FROM campaigns));
CREATE POLICY asker_can_see ON campaign_products FOR SELECT
    USING (campaign_id IN (SELECT campaign_id FROM campaigns));
CREATE POLICY asker_can_see ON campaign_customer_behavior FOR SELECT
    USING (campaign_id IN (SELECT campaign_id FROM campaigns));
