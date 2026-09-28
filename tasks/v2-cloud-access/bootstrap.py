"""Create only the reviewed v2 result-writer identity; never expose credentials."""

from __future__ import annotations

import argparse
import configparser
import json
from pathlib import Path
import secrets

ACCOUNT = "393229433969"
REGION = "ap-northeast-2"
SOURCE_USER = f"arn:aws:iam::{ACCOUNT}:user/junyoung727"
ROLE = "edge-analysis-v2-local-writer"
ROLE_ARN = f"arn:aws:iam::{ACCOUNT}:role/{ROLE}"
PROFILE = "edge-v2-writer"
SESSION_NAME = "v2-writer-junyoung727"
DOCUMENT = "EdgeV2-RdsWriterTunnel"
DOCUMENT_ARN = f"arn:aws:ssm:{REGION}:{ACCOUNT}:document/{DOCUMENT}"
BASTION = "i-0ba627536f36993d5"
INSTANCE_ARN = f"arn:aws:ec2:{REGION}:{ACCOUNT}:instance/{BASTION}"
HOST = "edge-dev.cru6wwggoppd.ap-northeast-2.rds.amazonaws.com"
SECRET_NAME = "edge/analysis-v2/writer"
DB_ROLE = "edge_analysis_v2_writer"
MARKER = "edge-analysis-v2-result-access"



def document() -> dict:
    """Return a port-only document with no caller-controlled destination."""
    return {"schemaVersion": "1.0", "description": MARKER, "sessionType": "Port", "parameters": {},
            "properties": {"host": HOST, "portNumber": "5432", "localPortNumber": "15433",
                           "type": "LocalPortForwarding"}}


def trust() -> dict:
    """Trust only the existing human identity with a fixed session name."""
    return {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"AWS": SOURCE_USER},
            "Action": "sts:AssumeRole", "Condition": {"StringEquals": {"sts:RoleSessionName": SESSION_NAME}}}]}


def policy(secret_arn: str) -> dict:
    """Allow one tunnel, own sessions and the dedicated database secret only."""
    return {"Version": "2012-10-17", "Statement": [
        {"Sid": "FixedNodeWithDocumentCheck", "Effect": "Allow", "Action": "ssm:StartSession",
         "Resource": [INSTANCE_ARN, DOCUMENT_ARN], "Condition": {"BoolIfExists": {"ssm:SessionDocumentAccessCheck": "true"}}},
        {"Sid": "OwnDataChannel", "Effect": "Allow", "Action": "ssmmessages:OpenDataChannel",
         "Resource": f"arn:aws:ssm:{REGION}:{ACCOUNT}:session/${{aws:userid}}-*"},
        {"Sid": "OwnSessionLifecycle", "Effect": "Allow", "Action": ["ssm:TerminateSession", "ssm:ResumeSession"],
         "Resource": f"arn:aws:ssm:{REGION}:{ACCOUNT}:session/*", "Condition": {"StringEquals": {
             "ssm:resourceTag/aws:ssmmessages:target-id": BASTION,
             "ssm:resourceTag/aws:ssmmessages:session-id": "${aws:userid}"}}},
        {"Sid": "DedicatedCredential", "Effect": "Allow", "Action": "secretsmanager:GetSecretValue", "Resource": secret_arn},
    ]}


def source_session():
    """Return the expected bootstrap identity or stop without mutating anything."""
    import boto3
    session = boto3.Session(profile_name="work", region_name=REGION)
    if session.client("sts").get_caller_identity()["Arn"] != SOURCE_USER:
        raise ValueError("Unexpected bootstrap identity")
    return session


def setup_aws(report: dict) -> None:
    """Create dedicated AWS resources; preserve and verify pre-existing resources."""
    from botocore.exceptions import ClientError
    session = source_session()
    iam, ssm, sm = (session.client(name) for name in ("iam", "ssm", "secretsmanager"))
    try:
        existing = iam.get_role(RoleName=ROLE)["Role"]
        if existing["Description"] != MARKER or existing["AssumeRolePolicyDocument"] != trust():
            raise ValueError("Existing IAM role is not the reviewed role")
        if iam.list_attached_role_policies(RoleName=ROLE)["AttachedPolicies"]:
            raise ValueError("Unexpected attached policies")
        names = iam.list_role_policies(RoleName=ROLE)["PolicyNames"]
        if set(names) - {"EdgeV2Writer"}:
            raise ValueError("Unexpected inline policies")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "NoSuchEntity":
            raise
        iam.create_role(RoleName=ROLE, AssumeRolePolicyDocument=json.dumps(trust()), Description=MARKER,
                        MaxSessionDuration=3600, Tags=[{"Key": "Purpose", "Value": MARKER}])
    report["role"] = ROLE_ARN
    try:
        raw = ssm.get_document(Name=DOCUMENT, DocumentFormat="JSON")
        if json.loads(raw["Content"]) != document():
            raise ValueError("Existing SSM document differs")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "InvalidDocument":
            raise
        ssm.create_document(Name=DOCUMENT, DocumentType="Session", DocumentFormat="JSON",
                            Content=json.dumps(document()), Tags=[{"Key": "Purpose", "Value": MARKER}])
    report["document"] = DOCUMENT_ARN
    try:
        metadata = sm.describe_secret(SecretId=SECRET_NAME)
        if metadata.get("Description") != MARKER:
            raise ValueError("Existing secret is not owned by this bootstrap")
        secret_arn = metadata["ARN"]
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceNotFoundException":
            raise
        credential = {"username": DB_ROLE, "password": secrets.token_urlsafe(36), "host": HOST, "port": 5432, "dbname": "edge"}
        secret_arn = sm.create_secret(Name=SECRET_NAME, Description=MARKER, SecretString=json.dumps(credential),
                                     Tags=[{"Key": "Purpose", "Value": MARKER}])["ARN"]
    report["secret_arn"] = secret_arn
    names = iam.list_role_policies(RoleName=ROLE)["PolicyNames"]
    if "EdgeV2Writer" in names:
        current = iam.get_role_policy(RoleName=ROLE, PolicyName="EdgeV2Writer")["PolicyDocument"]
        if current != policy(secret_arn):
            raise ValueError("Existing inline policy differs; not overwritten")
    else:
        iam.put_role_policy(RoleName=ROLE, PolicyName="EdgeV2Writer", PolicyDocument=json.dumps(policy(secret_arn)))
    report["policy_installed"] = True


def setup_database(report: dict) -> None:
    """Enable the Flyway-created writer using its dedicated secret.

    Requires the fixed writer tunnel and RDS CA beside this script. Existing
    login credentials are verified later, never silently rotated here.
    """
    import psycopg
    from psycopg import sql
    session = source_session()
    sm = session.client("secretsmanager")
    db = session.client("rds").describe_db_instances(DBInstanceIdentifier="edge-dev")["DBInstances"][0]
    if db["Endpoint"]["Address"] != HOST:
        raise ValueError("Unexpected database host")
    admin = json.loads(sm.get_secret_value(SecretId=db["MasterUserSecret"]["SecretArn"])["SecretString"])
    writer = json.loads(sm.get_secret_value(SecretId=SECRET_NAME)["SecretString"])
    expected = dict(username=DB_ROLE, host=HOST, port=5432, dbname="edge")
    if any(writer.get(key) != value for key, value in expected.items()) or not writer.get("password"):
        raise ValueError("Unexpected writer secret identity")
    ca = Path(__file__).with_name("ap-northeast-2-bundle.pem").resolve(strict=True)
    with psycopg.connect(host=HOST, hostaddr="127.0.0.1", port=15433, dbname="edge",
                        user=admin["username"], password=admin["password"],
                        sslmode="verify-full", sslrootcert=str(ca), connect_timeout=10, autocommit=True,
                        options="-c statement_timeout=15000") as conn:
        checks = Path(__file__).parents[2] / "src/libs/schema/tests/analysis_v2_writer.sql"
        with conn.cursor() as cursor:
            cursor.execute(checks.read_text(encoding="utf-8"), prepare=False)
            while cursor.nextset():
                pass
        row = conn.execute("SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolreplication, "
                           "rolbypassrls, rolinherit, shobj_description(oid,'pg_authid') "
                           "FROM pg_roles WHERE rolname=%s", (DB_ROLE,)).fetchone()
        if row is None or any(row[1:7]) or row[7] != "edge-analysis-v2-result-writer":
            raise ValueError("Flyway writer role missing or changed")
        if not row[0]:
            conn.execute(sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(DB_ROLE), sql.Literal(writer["password"])))
    report["writer_login_enabled"] = True


def install_profile(report: dict) -> None:
    """Append only a dedicated assumed-role profile, without copying access keys."""
    path = Path.home() / ".aws" / "config"
    config = configparser.RawConfigParser()
    config.read(path, encoding="utf-8")
    name = f"profile {PROFILE}"
    expected = {"role_arn": ROLE_ARN, "source_profile": "work", "role_session_name": SESSION_NAME,
                "region": REGION, "output": "json"}
    if config.has_section(name):
        if dict(config.items(name)) != expected:
            raise ValueError("Existing local profile differs; not overwritten")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as file:
            file.write("\n[" + name + "]\n" + "".join(f"{key} = {value}\n" for key, value in expected.items()))
    report["profile"] = PROFILE


def main() -> int:
    """Execute one reviewed stage and always save a credential-free status report."""
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["plan", "aws", "database", "profile"])
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = {"stage": args.stage, "status": "failed"}
    try:
        if args.stage == "plan":
            report.update(document=document(), trust=trust(), database_role=DB_ROLE)
        elif args.stage == "aws":
            setup_aws(report)
        elif args.stage == "database":
            setup_database(report)
        else:
            install_profile(report)
        report["status"] = "completed"
    except Exception as exc:
        report["error_type"] = type(exc).__name__
        if hasattr(exc, "response"):
            report["error_code"] = exc.response.get("Error", {}).get("Code")
            report["operation"] = getattr(exc, "operation_name", None)
        elif isinstance(exc, ValueError):
            report["reason"] = str(exc)
        else:
            report["sqlstate"] = getattr(exc, "sqlstate", None)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"status": report["status"], "stage": args.stage, "report": str(args.report),
                      "error_code": report.get("error_code"), "operation": report.get("operation")}))
    return 0 if report["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
