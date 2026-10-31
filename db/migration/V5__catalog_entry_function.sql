-- ============================================================================
--  V5 -- catalog_entry(): the document_catalog labels, available before the document row exists
--
--  Ingestion builds each chunk's contextual header from the catalog labels, embeds the chunks (a
--  network call that can take seconds), and only then writes documents + doc_chunks in one short
--  transaction. A view can only describe rows that already exist, so the labels move into a function
--  that takes the document's attributes directly, and the view now calls the same function: the labels
--  are still defined in exactly one place.
--
--  CREATE OR REPLACE VIEW keeps the view's columns, names and order unchanged.
-- ============================================================================

CREATE FUNCTION catalog_entry(p_doc_type TEXT, p_title TEXT, p_department TEXT, p_campaign_id BIGINT)
RETURNS TABLE (
    type_label       TEXT,
    department_label TEXT,
    campaign_name    TEXT,
    start_date       DATE,
    host_name        TEXT,
    display_name     TEXT
)
LANGUAGE sql STABLE AS $$
    SELECT
        CASE p_doc_type
            WHEN 'sop' THEN 'SOP'
            WHEN 'campaign_plan' THEN 'Campaign plan'
            WHEN 'campaign_review' THEN 'Post-campaign review'
        END,
        CASE WHEN p_department = 'ALL' THEN 'All departments' ELSE p_department END,
        c.name,
        c.start_date,
        h.name,
        CASE
            WHEN p_doc_type = 'sop' THEN
                concat_ws('_', 'SOP', CASE WHEN p_department = 'ALL' THEN 'All' ELSE p_department END, p_title)
            ELSE
                concat_ws('_', CASE p_doc_type WHEN 'campaign_plan' THEN 'Plan' ELSE 'Review' END,
                          to_char(c.start_date, 'YYYY-MM-DD'), p_department, h.name, c.name)
        END
    -- One row even for an SOP: the LEFT JOINs start from a single-row VALUES list, so a missing
    -- campaign gives NULLs instead of no row at all
    FROM (VALUES (p_campaign_id)) AS d (campaign_id)
    LEFT JOIN campaigns c ON c.campaign_id = d.campaign_id
    LEFT JOIN hosts h ON h.host_id = c.host_id
$$;

CREATE OR REPLACE VIEW document_catalog AS
SELECT
    d.doc_id,
    d.doc_type,
    d.title,
    d.department,
    d.campaign_id,
    e.type_label,
    e.department_label,
    e.campaign_name,
    e.start_date,
    e.host_name,
    e.display_name
FROM documents d
CROSS JOIN LATERAL catalog_entry(d.doc_type, d.title, d.department, d.campaign_id) e;
