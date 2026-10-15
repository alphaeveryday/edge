"""Provisioned AWS access must stay within the reviewed writer resources."""
import importlib.util
from pathlib import Path

path = Path(__file__).parents[5] / "tasks/v2-cloud-access/bootstrap.py"
spec = importlib.util.spec_from_file_location("writer_bootstrap", path)
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


def test_tunnel_has_no_caller_controlled_host_or_shell():
    doc = bootstrap.document()
    assert doc["sessionType"] == "Port" and doc["parameters"] == {}
    assert doc["properties"] == dict(host=bootstrap.HOST, portNumber="5432",
                                     localPortNumber="15433", type="LocalPortForwarding")


def test_only_dedicated_secret_and_fixed_tunnel_are_permitted():
    arn = "arn:aws:secretsmanager:ap-northeast-2:393229433969:secret:edge/analysis-v2/writer-test"
    statements = {s["Sid"]: s for s in bootstrap.policy(arn)["Statement"]}
    assert statements["DedicatedCredential"]["Resource"] == arn
    assert statements["DedicatedCredential"]["Action"] == "secretsmanager:GetSecretValue"
    assert statements["FixedNodeWithDocumentCheck"]["Resource"] == [bootstrap.INSTANCE_ARN, bootstrap.DOCUMENT_ARN]
    actions = set()
    for statement in statements.values():
        action = statement["Action"]
        actions.update([action] if isinstance(action, str) else action)
    assert actions == {"ssm:StartSession", "ssm:TerminateSession", "ssm:ResumeSession",
                       "ssmmessages:OpenDataChannel", "secretsmanager:GetSecretValue"}


def test_trust_is_limited_to_existing_human_and_named_session():
    statement, = bootstrap.trust()["Statement"]
    assert statement["Principal"] == {"AWS": bootstrap.SOURCE_USER}
    assert statement["Condition"] == {"StringEquals": {"sts:RoleSessionName": bootstrap.SESSION_NAME}}
