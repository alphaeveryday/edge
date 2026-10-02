# Ontology library

```text
src/edge_ontology/
  metadata/
    object_types/       # reserved; dashboard models remain in their repository
    link_types/         # reserved; participant roles are not Link Types
    value_types/        # event categories, roles, kinds, stages and labels
      event_types/
    shared_properties/  # existing common attribute definitions
  rules/
    threading/          # event identity contract
    arguments/          # participant slot bindings
  reference_data/       # authority registry entries, not type definitions
  backlog/              # unimplemented feature definitions
  oms/                  # read/validate definitions; no object-instance storage
    loader.py
    labels.py
    entity/
    properties/
    roles/
    events/
  constants.py
```

This is a layout migration, not a semantic rewrite. YAML bytes and vocabulary version are unchanged. Existing event documents retain their original schema. Public `from edge_ontology import ...` exports are preserved; internal module imports now use `edge_ontology.oms` and the in-repository caller is updated. No v1 engine is invoked.

Package data is loaded relative to `edge_ontology`, so YAML ships with the wheel. `oms` contains typed definitions, loaders and existing cross-reference validation. It is not a deployed metadata server. Object-instance writes and threading execution stay in data-pipeline.

Validation: `uv run --no-project --with pytest --with pyyaml --python 3.12 pytest src/libs/ontology/tests -c src/libs/ontology/pyproject.toml -q`.
