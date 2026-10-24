CREATE OR REPLACE VIEW ontology_view.financial_metric AS
-- Display context is additive; source identities, values and row scope are unchanged.
SELECT record.*,
       (company_e.display_name)::text AS company_name,
       (concat_ws(' / ', COALESCE(NULLIF(company_e.display_name,''), record.company_id, 'Unknown company'), record.fiscal_year::text, record.fs_basis, record.fiscal_period, record.period_kind, record.metric, record.derivation, to_char(record.received_at AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS.US') || ' UTC'))::text AS display_title
FROM (
SELECT mapped.*,
       mapped.value::text AS value_decimal_text
FROM (
SELECT ('[' || to_json(base.corp_code::text)::text || ',' || to_json(base.fiscal_year::text)::text || ',' || to_json(base.fiscal_period::text)::text || ',' || to_json(base.metric::text)::text || ',' || to_json(base.period_kind::text)::text || ',' || to_json(base.fs_basis::text)::text || ',' || to_json(base.raw_run_id::text)::text || ']')::text AS id,
       (base.corp_code)::text AS corp_code,
       (base.instrument_code)::text AS instrument_code,
       (base.fiscal_year)::smallint AS fiscal_year,
       (base.fiscal_period)::text AS fiscal_period,
       (base.period_end)::date AS period_end,
       (base.metric)::text AS metric,
       (base.period_kind)::text AS period_kind,
       (base.fs_basis)::text AS fs_basis,
       (base.derivation)::text AS derivation,
       (base.value)::numeric AS value,
       (base.unit)::text AS unit,
       (base.formula)::text AS formula,
       (base.rcept_no)::text AS rcept_no,
       (base.available_at)::timestamptz AS available_at,
       (base.received_at)::timestamptz AS received_at,
       (base.availability_basis)::text AS availability_basis,
       (base.inputs)::text AS calculation_inputs,
       (SELECT c.actor_id FROM public.company_profile c WHERE c.dart_corp_code=base.corp_code)::text AS company_id,
       (SELECT '[' || to_json(r.corp_code::text)::text || ',' || to_json(r.fiscal_year::text)::text || ',' || to_json(r.reprt_code::text)::text || ',' || to_json(r.fs_basis::text)::text || ',' || to_json(r.raw_run_id::text)::text || ']' FROM public.financial_report_version r WHERE r.corp_code=base.corp_code AND r.fiscal_year=base.fiscal_year AND r.fs_basis=base.fs_basis AND r.raw_run_id=base.raw_run_id AND r.reprt_code=CASE base.fiscal_period WHEN 'Q1' THEN '11013' WHEN 'Q2' THEN '11012' WHEN 'Q3' THEN '11014' WHEN 'Q4' THEN '11011' WHEN 'FY' THEN '11011' END AND (base.rcept_no IS NULL OR r.rcept_no=base.rcept_no))::text AS reporting_context_id,
       (SELECT '[' || to_json(r.corp_code::text)::text || ',' || to_json(r.fiscal_year::text)::text || ',' || to_json(r.reprt_code::text)::text || ',' || to_json(r.fs_basis::text)::text || ',' || to_json(r.raw_run_id::text)::text || ']' FROM public.financial_report_version r WHERE r.corp_code=base.corp_code AND r.fiscal_year=base.fiscal_year AND r.fs_basis=base.fs_basis AND r.raw_run_id=base.raw_run_id AND r.reprt_code=CASE base.fiscal_period WHEN 'Q1' THEN '11013' WHEN 'Q2' THEN '11012' WHEN 'Q3' THEN '11014' WHEN 'Q4' THEN '11011' WHEN 'FY' THEN '11011' END AND base.derivation='REPORTED' AND r.rcept_no=base.rcept_no)::text AS reported_snapshot_id
FROM public.financial_metric AS base
) AS mapped
) AS record
LEFT JOIN public.entity company_e ON company_e.entity_id=record.company_id;
