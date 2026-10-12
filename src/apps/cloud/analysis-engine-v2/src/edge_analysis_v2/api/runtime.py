"""Lambda entrypoint; credentials and network destinations remain server-owned."""
import json
import os
from pathlib import Path

import boto3
import psycopg

from edge_analysis_v2.api.publications import PublicationReader
from edge_analysis_v2.api.service import AnalysisAPI
from edge_analysis_v2.storage.database import DB_HOST


def connection():
    """Open TLS-verified PostgreSQL with the dedicated result-reader identity."""
    secret=json.loads(boto3.client('secretsmanager').get_secret_value(
        SecretId=os.environ['RESULT_READER_SECRET_ARN'])['SecretString'])
    expected={'host':DB_HOST,'port':5432,'dbname':'edge','username':'edge_analysis_v2_api_reader'}
    if any(secret.get(k)!=v for k,v in expected.items()) or not secret.get('password'):
        raise ValueError('Unexpected API database identity')
    return psycopg.connect(host=DB_HOST,port=5432,dbname='edge',user=expected['username'],
        password=secret['password'],sslmode='verify-full',
        sslrootcert=str(Path(os.environ['RDS_CA_PATH']).resolve(strict=True)),
        autocommit=True,connect_timeout=5,
        options='-c default_transaction_read_only=on -c statement_timeout=5000 -c idle_in_transaction_session_timeout=10000')


def handler(event, context):
    """Serve API Gateway HTTP API payload 2.0; no model call runs in Lambda."""
    api=AnalysisAPI(PublicationReader(connection),boto3.client('stepfunctions'),os.environ['STATE_MACHINE_ARN'])
    return api.handle(event)
