-- Run after applying company.sql and equity.sql, in a read-only transaction.
DO $$
BEGIN
  IF EXISTS (SELECT id FROM ontology_view.company GROUP BY id HAVING count(*) <> 1 OR id IS NULL)
     OR EXISTS (SELECT id FROM ontology_view.equity GROUP BY id HAVING count(*) <> 1 OR id IS NULL) THEN
    RAISE EXCEPTION 'Object identity is missing or duplicated';
  END IF;
  IF EXISTS (
    (SELECT id FROM ontology_view.company EXCEPT SELECT actor_id FROM public.company_profile)
    UNION ALL
    (SELECT actor_id FROM public.company_profile EXCEPT SELECT id FROM ontology_view.company)
  ) THEN
    RAISE EXCEPTION 'Company view changed the set of company identities';
  END IF;
  IF EXISTS (
    (SELECT e.id, c.id FROM ontology_view.equity e JOIN ontology_view.company c ON c.id=e.issuer_id
     EXCEPT SELECT instrument_id, issuer_actor_id FROM public.equity_profile)
    UNION ALL
    (SELECT instrument_id, issuer_actor_id FROM public.equity_profile
     EXCEPT SELECT e.id, c.id FROM ontology_view.equity e JOIN ontology_view.company c ON c.id=e.issuer_id)
  ) THEN
    RAISE EXCEPTION 'Issuer links differ from the stored issuing-company relationship';
  END IF;
END $$;
