-- ============================================================================
--  V9 -- the data dictionary and the metric views, for Text-to-SQL
--
--  The comments are the data dictionary: what each table and column means in the business, its unit, and what it
--  is not. llm-service reads them from the catalog at run time (app/sql_agent/schema.py), so a model writing SQL
--  sees the same definitions a person reads here, and changing one is a migration, reviewed like any other.
--
--  The views hold the definitions a model would otherwise have to work out on every question: which joins give a
--  campaign's department and host, how its daily rows add up, and ROI. ROI is a fixed formula over uploaded
--  numbers (docs/data-schema.md); it took two corrections, so it is written once, here. The review documents keep
--  the explanation of why a campaign did well or badly, which no column can hold.
--
--  security_invoker: a view normally reads its tables as the view's owner, and the owner isn't subject to row-level
--  security. With security_invoker the tables are read as whoever queries the view, so row-level security on the
--  tables also holds through the views (PostgreSQL 15 and later).
--
--  ip_address is in no view: it is personal data no question needs.
-- ============================================================================

-- ----------------------------------------------------------------------------
--  The tables
-- ----------------------------------------------------------------------------
COMMENT ON TABLE departments IS 'Operations departments. Every campaign, host and document belongs to one; staff and managers see only their own department''s data.';
COMMENT ON COLUMN departments.name IS 'Department name, for example Beauty or Fresh Grocery.';

COMMENT ON TABLE hosts IS 'Livestream hosts. A host belongs to one department.';
COMMENT ON COLUMN hosts.name IS 'The host''s full name.';
COMMENT ON COLUMN hosts.department_id IS 'The department the host works for.';
COMMENT ON COLUMN hosts.category IS 'The product category the host sells; the same as the department name.';
COMMENT ON COLUMN hosts.fans_count IS 'Followers of the host''s channel, as last synced.';
COMMENT ON COLUMN hosts.join_date IS 'The day the host joined.';

COMMENT ON TABLE campaigns IS 'Livestream campaigns. One row per campaign; several campaigns can share a name (the same event in different months), so a campaign is told apart by its name and start_date.';
COMMENT ON COLUMN campaigns.name IS 'Campaign name, for example Beauty Black Friday Live. Not unique.';
COMMENT ON COLUMN campaigns.department_id IS 'The department that owns the campaign.';
COMMENT ON COLUMN campaigns.host_id IS 'The lead host. Co-hosts are not recorded.';
COMMENT ON COLUMN campaigns.category IS 'The product category; the same as the department name.';
COMMENT ON COLUMN campaigns.start_date IS 'First day of the campaign.';
COMMENT ON COLUMN campaigns.end_date IS 'Last day of the campaign.';
COMMENT ON COLUMN campaigns.status IS 'planned, running or finished. Only finished campaigns have an actual operating cost and product sales.';
COMMENT ON COLUMN campaigns.has_plan_doc IS 'Whether a campaign plan document exists. A shortcut for routing, not data about the campaign.';
COMMENT ON COLUMN campaigns.has_review_doc IS 'Whether a post-campaign review document exists. A shortcut for routing, not data about the campaign.';
COMMENT ON COLUMN campaigns.planned_operating_cost IS 'Budgeted operating cost in US dollars: placement fees, promotion and giveaways. Product cost is not included.';
COMMENT ON COLUMN campaigns.actual_operating_cost IS 'Actual operating cost in US dollars, same scope as the planned cost. NULL until the campaign is finished and its invoices are in.';

COMMENT ON TABLE campaign_metrics IS 'One row per campaign per day it ran: the livestream''s daily numbers.';
COMMENT ON COLUMN campaign_metrics.campaign_id IS 'The campaign.';
COMMENT ON COLUMN campaign_metrics.date IS 'The day.';
COMMENT ON COLUMN campaign_metrics.gmv IS 'Gross merchandise value that day, in US dollars: everything sold in the livestream, before returns. A campaign''s GMV is the sum over its days.';
COMMENT ON COLUMN campaign_metrics.dau IS 'Daily active viewers: distinct viewers who watched that day.';
COMMENT ON COLUMN campaign_metrics.watch_time IS 'Average watch time per viewer that day, in minutes.';
COMMENT ON COLUMN campaign_metrics.conversion_rate IS 'Share of that day''s viewers who bought something, as a fraction (0.0874 is 8.74%).';
COMMENT ON COLUMN campaign_metrics.peak_concurrent_viewers IS 'The most viewers watching at the same moment that day.';
COMMENT ON COLUMN campaign_metrics.avg_dwell_time_minutes IS 'Average minutes a viewer stayed in the livestream that day.';

COMMENT ON TABLE campaign_products IS 'Sales of the featured products of a finished campaign, one row per product, as uploaded by operations. Featured products don''t add up to the campaign''s GMV: the rest is long-tail products.';
COMMENT ON COLUMN campaign_products.campaign_id IS 'The campaign.';
COMMENT ON COLUMN campaign_products.product_name IS 'Product name, for example Serum. There is no product catalog.';
COMMENT ON COLUMN campaign_products.units_sold IS 'Units sold during the campaign.';
COMMENT ON COLUMN campaign_products.sell_through_rate IS 'Share of the stock prepared for the campaign that sold, as a fraction.';
COMMENT ON COLUMN campaign_products.return_rate IS 'Share of units sold that were returned, as a fraction.';
COMMENT ON COLUMN campaign_products.sales_amount IS 'Sales of this product in US dollars.';
COMMENT ON COLUMN campaign_products.click_through_rate IS 'Share of viewers shown the product who clicked it, as a fraction.';
COMMENT ON COLUMN campaign_products.cost_price IS 'Cost of one unit in US dollars.';
COMMENT ON COLUMN campaign_products.profit IS 'Profit on this product in the campaign, in US dollars: sales minus product cost. Operating cost is not taken off.';
COMMENT ON COLUMN campaign_products.profit_margin IS 'Profit divided by sales, as a fraction.';

COMMENT ON TABLE campaign_customer_behavior IS 'A sample of a finished campaign''s customers, one row per customer, as uploaded by operations. A sample, not every customer: its totals don''t match the campaign''s sales.';
COMMENT ON COLUMN campaign_customer_behavior.campaign_id IS 'The campaign.';
COMMENT ON COLUMN campaign_customer_behavior.customer_ref IS 'Anonymized customer ID. The same customer has the same ID in every campaign, so repeat customers can be counted.';
COMMENT ON COLUMN campaign_customer_behavior.region IS 'The customer''s region, for example Midwest.';
COMMENT ON COLUMN campaign_customer_behavior.dwell_time_minutes IS 'Minutes this customer stayed in the livestream.';
COMMENT ON COLUMN campaign_customer_behavior.products_purchased IS 'JSON array of the product names the customer bought.';
COMMENT ON COLUMN campaign_customer_behavior.purchase_amount IS 'What the customer spent in the campaign, in US dollars.';
COMMENT ON COLUMN campaign_customer_behavior.ip_address IS 'Personal data. Never shown to a model and in no view.';

-- ----------------------------------------------------------------------------
--  The views
-- ----------------------------------------------------------------------------
-- The daily rows and the product rows are summed in separate subqueries: joined together they would multiply each
-- other, and every sum would come out several times too large
CREATE VIEW campaign_summary WITH (security_invoker = true) AS
SELECT
    c.campaign_id,
    c.name AS campaign_name,
    d.name AS department,
    h.name AS host_name,
    c.start_date,
    c.end_date,
    c.status,
    c.planned_operating_cost,
    c.actual_operating_cost,
    m.days_reported,
    m.total_gmv,
    m.avg_conversion_rate,
    m.peak_concurrent_viewers,
    m.avg_dau,
    m.avg_watch_time,
    m.avg_dwell_time_minutes,
    p.products_featured,
    p.total_units_sold,
    p.total_product_sales,
    p.total_profit,
    p.total_profit / NULLIF(c.actual_operating_cost, 0) AS roi
FROM campaigns c
JOIN departments d ON d.department_id = c.department_id
JOIN hosts h ON h.host_id = c.host_id
LEFT JOIN LATERAL (
    SELECT count(*) AS days_reported,
           sum(gmv) AS total_gmv,
           avg(conversion_rate) AS avg_conversion_rate,
           max(peak_concurrent_viewers) AS peak_concurrent_viewers,
           avg(dau) AS avg_dau,
           avg(watch_time) AS avg_watch_time,
           avg(avg_dwell_time_minutes) AS avg_dwell_time_minutes
    FROM campaign_metrics
    WHERE campaign_id = c.campaign_id
) m ON true
LEFT JOIN LATERAL (
    SELECT count(*) AS products_featured,
           sum(units_sold) AS total_units_sold,
           sum(sales_amount) AS total_product_sales,
           sum(profit) AS total_profit
    FROM campaign_products
    WHERE campaign_id = c.campaign_id
) p ON true;

COMMENT ON VIEW campaign_summary IS 'One row per campaign with its department, host, costs and totals. Several campaigns can share a name: tell them apart by start_date.';
COMMENT ON COLUMN campaign_summary.department IS 'The department that owns the campaign.';
COMMENT ON COLUMN campaign_summary.host_name IS 'The lead host.';
COMMENT ON COLUMN campaign_summary.status IS 'planned, running or finished. Only finished campaigns have an actual operating cost, product sales and ROI.';
COMMENT ON COLUMN campaign_summary.planned_operating_cost IS 'Budgeted operating cost in US dollars: placement fees, promotion and giveaways, without product cost.';
COMMENT ON COLUMN campaign_summary.actual_operating_cost IS 'Actual operating cost in US dollars, same scope as the plan. NULL until the campaign is finished.';
COMMENT ON COLUMN campaign_summary.days_reported IS 'Days with daily numbers so far.';
COMMENT ON COLUMN campaign_summary.total_gmv IS 'GMV in US dollars: the sum of the daily GMV.';
COMMENT ON COLUMN campaign_summary.avg_conversion_rate IS 'The mean of the daily conversion rates, each day counting the same, as a fraction. Averaging several campaigns'' values gives each campaign the same weight; averaging their daily rows instead gives longer campaigns more.';
COMMENT ON COLUMN campaign_summary.peak_concurrent_viewers IS 'The most viewers at the same moment on any day of the campaign.';
COMMENT ON COLUMN campaign_summary.avg_dau IS 'The mean of the daily active viewers.';
COMMENT ON COLUMN campaign_summary.avg_watch_time IS 'The mean of the daily average watch time, in minutes.';
COMMENT ON COLUMN campaign_summary.avg_dwell_time_minutes IS 'The mean of the daily average dwell time, in minutes.';
COMMENT ON COLUMN campaign_summary.products_featured IS 'Featured products with uploaded sales; 0 before the campaign is finished.';
COMMENT ON COLUMN campaign_summary.total_units_sold IS 'Units of the featured products sold.';
COMMENT ON COLUMN campaign_summary.total_product_sales IS 'Sales of the featured products in US dollars; less than the GMV, which also counts long-tail products.';
COMMENT ON COLUMN campaign_summary.total_profit IS 'Profit on the featured products in US dollars, after product cost and before operating cost.';
COMMENT ON COLUMN campaign_summary.roi IS 'Return on the operating spend: total_profit / actual_operating_cost (1.5 means $1.50 of profit per dollar spent). NULL until the actual cost is in.';

CREATE VIEW campaign_daily_metrics WITH (security_invoker = true) AS
SELECT
    m.campaign_id,
    c.name AS campaign_name,
    d.name AS department,
    c.start_date AS campaign_start_date,
    m.date,
    m.gmv,
    m.dau,
    m.watch_time,
    m.conversion_rate,
    m.peak_concurrent_viewers,
    m.avg_dwell_time_minutes
FROM campaign_metrics m
JOIN campaigns c ON c.campaign_id = m.campaign_id
JOIN departments d ON d.department_id = c.department_id;

COMMENT ON VIEW campaign_daily_metrics IS 'One row per campaign per day it ran, with the campaign''s name and department.';
COMMENT ON COLUMN campaign_daily_metrics.campaign_start_date IS 'The campaign''s first day, to tell campaigns of the same name apart.';
COMMENT ON COLUMN campaign_daily_metrics.gmv IS 'Gross merchandise value that day, in US dollars, before returns.';
COMMENT ON COLUMN campaign_daily_metrics.dau IS 'Daily active viewers: distinct viewers who watched that day.';
COMMENT ON COLUMN campaign_daily_metrics.watch_time IS 'Average watch time per viewer that day, in minutes.';
COMMENT ON COLUMN campaign_daily_metrics.conversion_rate IS 'Share of that day''s viewers who bought something, as a fraction.';
COMMENT ON COLUMN campaign_daily_metrics.peak_concurrent_viewers IS 'The most viewers watching at the same moment that day.';
COMMENT ON COLUMN campaign_daily_metrics.avg_dwell_time_minutes IS 'Average minutes a viewer stayed that day.';

CREATE VIEW campaign_product_sales WITH (security_invoker = true) AS
SELECT
    p.campaign_id,
    c.name AS campaign_name,
    d.name AS department,
    c.start_date AS campaign_start_date,
    p.product_name,
    p.units_sold,
    p.sell_through_rate,
    p.return_rate,
    p.sales_amount,
    p.click_through_rate,
    p.cost_price,
    p.profit,
    p.profit_margin
FROM campaign_products p
JOIN campaigns c ON c.campaign_id = p.campaign_id
JOIN departments d ON d.department_id = c.department_id;

COMMENT ON VIEW campaign_product_sales IS 'Sales of each featured product of a finished campaign, with the campaign''s name and department.';
COMMENT ON COLUMN campaign_product_sales.campaign_start_date IS 'The campaign''s first day, to tell campaigns of the same name apart.';
COMMENT ON COLUMN campaign_product_sales.sell_through_rate IS 'Share of the stock prepared for the campaign that sold, as a fraction.';
COMMENT ON COLUMN campaign_product_sales.return_rate IS 'Share of units sold that were returned, as a fraction.';
COMMENT ON COLUMN campaign_product_sales.sales_amount IS 'Sales of this product in US dollars.';
COMMENT ON COLUMN campaign_product_sales.click_through_rate IS 'Share of viewers shown the product who clicked it, as a fraction.';
COMMENT ON COLUMN campaign_product_sales.cost_price IS 'Cost of one unit in US dollars.';
COMMENT ON COLUMN campaign_product_sales.profit IS 'Profit on this product in US dollars: sales minus product cost, before operating cost.';
COMMENT ON COLUMN campaign_product_sales.profit_margin IS 'Profit divided by sales, as a fraction.';

CREATE VIEW campaign_customers WITH (security_invoker = true) AS
SELECT
    b.campaign_id,
    c.name AS campaign_name,
    d.name AS department,
    c.start_date AS campaign_start_date,
    b.customer_ref,
    b.region,
    b.dwell_time_minutes,
    b.products_purchased,
    b.purchase_amount
FROM campaign_customer_behavior b
JOIN campaigns c ON c.campaign_id = b.campaign_id
JOIN departments d ON d.department_id = c.department_id;

COMMENT ON VIEW campaign_customers IS 'A sample of each finished campaign''s customers, not all of them: totals don''t match the campaign''s sales, so say so when using it.';
COMMENT ON COLUMN campaign_customers.campaign_start_date IS 'The campaign''s first day, to tell campaigns of the same name apart.';
COMMENT ON COLUMN campaign_customers.customer_ref IS 'Anonymized customer ID, the same in every campaign, so repeat customers can be counted.';
COMMENT ON COLUMN campaign_customers.region IS 'The customer''s region.';
COMMENT ON COLUMN campaign_customers.dwell_time_minutes IS 'Minutes this customer stayed in the livestream.';
COMMENT ON COLUMN campaign_customers.products_purchased IS 'JSON array of the product names the customer bought.';
COMMENT ON COLUMN campaign_customers.purchase_amount IS 'What the customer spent in the campaign, in US dollars.';

CREATE VIEW host_profiles WITH (security_invoker = true) AS
SELECT
    h.host_id,
    h.name AS host_name,
    d.name AS department,
    h.fans_count,
    h.join_date
FROM hosts h
JOIN departments d ON d.department_id = h.department_id;

COMMENT ON VIEW host_profiles IS 'Livestream hosts and their departments.';
COMMENT ON COLUMN host_profiles.fans_count IS 'Followers of the host''s channel, as last synced.';
COMMENT ON COLUMN host_profiles.join_date IS 'The day the host joined.';
