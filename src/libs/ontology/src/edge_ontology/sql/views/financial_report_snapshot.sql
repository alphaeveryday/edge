CREATE OR REPLACE VIEW ontology_view.financial_report_snapshot AS
-- Display context is additive; source identities, values and row scope are unchanged.
SELECT record.*,
       (company_e.display_name)::text AS company_name,
       (concat_ws(' / ', COALESCE(NULLIF(company_e.display_name,''), record.company_id, 'Unknown company'), record.fiscal_year::text, record.fs_basis, record.report_coverage, record.receipt_number, to_char(record.received_at AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS.US') || ' UTC'))::text AS display_title
FROM (
SELECT ('[' || to_json(base.corp_code::text)::text || ',' || to_json(base.fiscal_year::text)::text || ',' || to_json(base.reprt_code::text)::text || ',' || to_json(base.fs_basis::text)::text || ',' || to_json(base.raw_run_id::text)::text || ']')::text AS id,
       (base.corp_code)::text AS corp_code,
       (base.instrument_code)::text AS instrument_code,
       (base.fiscal_year)::smallint AS fiscal_year,
       (base.reprt_code)::text AS report_type_code,
       (CASE base.report_period WHEN 'Q1' THEN 'Q1' WHEN 'Q2' THEN 'H1' WHEN 'Q3' THEN '9M' WHEN 'Q4' THEN 'FY' END)::text AS report_coverage,
       (base.fs_basis)::text AS fs_basis,
       (base.rcept_no)::text AS receipt_number,
       (base.rcept_date)::date AS receipt_date,
       (base.available_at)::timestamptz AS available_at,
       (base.received_at)::timestamptz AS received_at,
       (base.availability_basis)::text AS availability_basis,
       (SELECT c.actor_id FROM public.company_profile c WHERE c.dart_corp_code=base.corp_code)::text AS company_id
FROM public.financial_report_version AS base
) AS record
LEFT JOIN public.entity company_e ON company_e.entity_id=record.company_id;
