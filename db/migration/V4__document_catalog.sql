-- ============================================================================
--  V4 -- document_catalog: display names and header labels for documents
--
--  One row per document with
--    display_name   SOP_<department or All>_<title>
--                   Plan_<start date>_<department>_<host>_<campaign>  /  Review_<...same...>
--                   for citations in answers, chunk headers and, later, storage paths
--    type_label, department_label, campaign_name, start_date, host_name
--                   the parts the contextual chunk header is built from (app/rag/chunking.py)
--
--  A view, not stored columns: the parts live in documents / campaigns / hosts, and a stored copy
--  would go stale the moment a campaign or host is renamed. The labels are defined only here, so
--  Python and SQL can't disagree about them. The names are for people to read; retrieval filters on
--  the structured columns (department, campaign_id, doc_type), never on a display name.
-- ============================================================================

CREATE VIEW document_catalog AS
SELECT
    d.doc_id,
    d.doc_type,
    d.title,
    d.department,
    d.campaign_id,
    CASE d.doc_type
        WHEN 'sop' THEN 'SOP'
        WHEN 'campaign_plan' THEN 'Campaign plan'
        WHEN 'campaign_review' THEN 'Post-campaign review'
    END AS type_label,
    CASE WHEN d.department = 'ALL' THEN 'All departments' ELSE d.department END AS department_label,
    c.name AS campaign_name,
    c.start_date,
    h.name AS host_name,
    CASE
        WHEN d.doc_type = 'sop' THEN
            concat_ws('_', 'SOP', CASE WHEN d.department = 'ALL' THEN 'All' ELSE d.department END, d.title)
        ELSE
            concat_ws('_', CASE d.doc_type WHEN 'campaign_plan' THEN 'Plan' ELSE 'Review' END,
                      to_char(c.start_date, 'YYYY-MM-DD'), d.department, h.name, c.name)
    END AS display_name
FROM documents d
-- LEFT JOIN: an SOP has no campaign. Plans and reviews always have one (chk_document_campaign_link),
-- and every campaign has a host (NOT NULL foreign key), so for them the joins always match
LEFT JOIN campaigns c ON c.campaign_id = d.campaign_id
LEFT JOIN hosts h ON h.host_id = c.host_id;
