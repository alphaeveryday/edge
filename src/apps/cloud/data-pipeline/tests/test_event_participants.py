"""A company participates in an event; its listed share is not the participant."""
import json
from unittest.mock import MagicMock

import pytest

from data_pipeline.events import participants


def connection(mapping):
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value.fetchall.return_value = list(mapping.items())
    return conn


def test_actor_conversion_keeps_roles_mentions_and_unresolved_participants():
    rows = [
        ("event", "SUPPLIER", "share", .8, "subject", "Company", "COMPANY_ENTITY", 0),
        ("event", "CUSTOMER", "organization", .9, "object", "Buyer", "ORGANIZATION", 0),
        ("event", "CUSTOMER", None, .5, "object", "Unknown", "COMPANY_ENTITY", 1),
        ("event", "PRODUCT", "concept", .9, "object", "Chip", "PRODUCT_OR_CONCEPT", 0),
    ]
    result = participants.actor_arguments(connection({"share": "issuer"}), rows)
    assert result == [rows[0][:2] + ("issuer",) + rows[0][3:], *rows[1:]]
    assert rows[0][2] == "share"  # Never mutate source extraction evidence.


def test_share_classes_cannot_silently_discard_different_role_evidence():
    rows = [("event", "ISSUER", share, .8, "subject", share, "COMPANY_ENTITY", 0)
            for share in ("common", "preferred")]
    with pytest.raises(ValueError, match="participant collision"):
        participants.actor_arguments(connection({"common": "issuer", "preferred": "issuer"}), rows)


def test_thread_identity_uses_same_actor_for_scalar_and_multi_party_values():
    values = {"SUPPLIER": "share", "CUSTOMER": json.dumps(["other", "share", "preferred"]),
              "CONTRACT_OBJECT": "concept"}
    result = participants.actor_role_values(values, {"share": "issuer", "preferred": "issuer"})
    assert result == {"SUPPLIER": "issuer", "CUSTOMER": '["issuer", "other"]',
                      "CONTRACT_OBJECT": "concept"}
    assert values["SUPPLIER"] == "share"


def test_duplicate_share_class_identities_become_one_company():
    assert participants.actor_role_values({"ISSUER": '["common", "preferred"]'},
                                         {"common": "issuer", "preferred": "issuer"}) == {"ISSUER": "issuer"}


def test_disclosure_persistence_uses_actors_without_changing_event_identity(monkeypatch):
    from test_assemble_disclosure_events import _Conn, _fact, _batch
    from data_pipeline.steps import assemble_disclosure_events as module
    conn = _Conn()
    conn.issuer_mapping = {"inst_supplier": "actor_supplier", "inst_customer": "actor_customer"}
    monkeypatch.setattr(module, "thread_events", lambda *_: 0)
    expected_id = module.to_canonical_event(_fact())["source_event_id"]
    module.persist_facts(conn, [_fact()])
    assert _batch(conn, "source_event")[0][0] == expected_id
    assert {r[1]: r[2] for r in _batch(conn, "event_argument")}["SUPPLIER"] == "actor_supplier"
    # Security matching still points at the traded instrument.
    assert _batch(conn, "document_entity")[0][1] == "inst_supplier"


def test_actor_identity_reuses_historical_thread_id_and_novelty():
    from test_assemble_events import _FakeConn, _batch, _multi_role_event
    from data_pipeline.steps import assemble_events as module
    conn = _FakeConn(prior_thread_counts={"historical_thread": 4})
    conn.issuer_mapping = {"share": "actor"}
    key = "event_type_id=COMPANY.CAPITAL.DIVIDEND_DECISION||required:ISSUER=actor"
    conn.thread_ids = {key: "historical_thread"}
    event = _multi_role_event("new_event", {"ISSUER": "share"},
                              etype="COMPANY.CAPITAL.DIVIDEND_DECISION")
    module.thread_events(conn, [event])
    [link] = _batch(conn, "event_thread_link")
    assert link[1] == "historical_thread"
    assert link[3] == "DUPLICATE_REBROADCAST"
    assert _batch(conn, "thread_discovery_snapshot")[0][2] == 4
    assert _batch(conn, "event_thread")[0][1] == key


def test_news_anchor_is_an_actor_while_document_matching_keeps_its_security(tmp_path, monkeypatch):
    from test_assemble_events import (_FakeConn, _batch, _assertion_rows_for, _setup,
                                      _write_news, _article, _default_llm, _db)
    from data_pipeline.lake import LocalStorage
    from data_pipeline.steps import assemble_events as module
    storage = LocalStorage(tmp_path / "lake")
    _write_news(storage, "ko", "2026-07-15", [_article("a1")])
    conn = _FakeConn(assertion_rows=_assertion_rows_for("a1"))
    conn.issuer_mapping = {dict(conn.instruments)["005930"]: "actor_samsung"}
    _setup(monkeypatch, conn)
    assert module.run(storage, "actor_test", db=_db(), complete_fn=_default_llm("a1"),
                      from_date="2026-07-15", to_date="2026-07-15") == 0
    assert _batch(conn, "event_argument")[0][2] == "actor_samsung"
    assert _batch(conn, "document_entity")[0][1] == dict(conn.instruments)["005930"]
