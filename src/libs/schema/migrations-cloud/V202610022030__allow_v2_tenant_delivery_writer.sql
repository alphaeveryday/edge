-- The result writer can append v2 deliveries, but cannot edit or forge v1 references.
GRANT SELECT (tenant_id) ON tenant TO edge_analysis_v2_writer;
GRANT SELECT (tenant_id, cursor, movement_analysis_id) ON tenant_delivery TO edge_analysis_v2_writer;
GRANT INSERT (tenant_id, cursor, delivery_type, movement_analysis_id)
    ON tenant_delivery TO edge_analysis_v2_writer;
