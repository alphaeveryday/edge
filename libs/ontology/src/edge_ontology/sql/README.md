# Ontology backing views

These ordinary PostgreSQL views preserve the existing object identities and expose the keys needed for graph edges. They do not modify ingestion tables or copy records into graph storage. Apply only reviewed files from `views/` inside a transaction, in an existing EDGE development database with the corresponding `public` source tables.

Create the `ontology_view` schema first. Grant schema usage and SELECT on the applied views to the existing read-only query role. View PK/FK roles are contracts checked by validation; PostgreSQL views do not create those constraints.

The Company–Equity path uses `company.id`, `equity.id`, and `equity.issuer_id`. The equity view supplies both Equity nodes and Company→Equity edges. No separate issuer-relation view or reverse-edge copy is necessary. Run `check_core.sql` after application; it raises an error on missing/duplicate identities or a changed issuer relationship.

Core cloud verification on 2026-10-05: 2,766 companies, 2,766 equities, and 2,766 issuer pairs matched the source. Forward and reverse PuppyGraph queries returned the same exact pairs. The pilot used PuppyGraph 1.13.0, image digest `sha256:8e362b158d8dde1ae9ed6df54f6751630af72a47d061bb2db42fa16547b368f7`, with external PostgreSQL reads and no local replication. These results establish correctness for this path and dataset, not a performance advantage or whole-ontology completion.

Deployment credentials and environment-specific launch scripts are intentionally not part of the SQL package. Roll back a newly created view with an explicit `DROP VIEW ontology_view.<name>` in a transaction, after checking its dependents; do not use CASCADE.
