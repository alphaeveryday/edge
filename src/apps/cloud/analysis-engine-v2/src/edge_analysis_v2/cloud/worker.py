"""One real-data cloud execution with durable incremental observations."""
import argparse
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
from threading import Event, Thread

import boto3
from psycopg.rows import dict_row

from edge_analysis_v2.analysis.service import execute_request
from edge_analysis_v2.cloud.artifacts import Publisher, atomic, encode
from edge_analysis_v2.cloud.contract import decode_request
from edge_analysis_v2.contracts.audit import read_contract_audit
from edge_analysis_v2.dashboard.server import assemble_screen
from edge_analysis_v2.sources.database import DatabaseTools, connect_sources, load_source, load_flow, load_prices, load_research_observations
from edge_analysis_v2.storage.database import connect_results
from edge_analysis_v2.storage.delivery import enqueue_movement
from edge_analysis_v2.storage.inspection import read_analysis_evidence, read_storage

LOG = logging.getLogger(__name__)


def export_records(connection, request, folder):
    """Export committed database records without changing the screen or tool schemas."""
    kind, identity = request['kind'], request['analysis_id']
    evidence = read_analysis_evidence(connection, kind, identity)
    if evidence is None:
        return None
    atomic(folder/'evidence.json',encode(evidence))
    atomic(folder/'storage.json',encode(read_storage(connection,kind,identity)))
    audit = read_contract_audit(connection,kind,identity)
    atomic(folder/'contract_audit.json',encode(audit))
    if evidence['analysis']['status']=='completed':
        features = ['all','summary','detail'] if kind=='movement' else ['all','summary','detail','factors','conclusion','factor_details']
        screens = {feature:assemble_screen(connection,kind,identity,feature) for feature in features}
        atomic(folder/'screens.json',encode(screens))
        atomic(folder/'screen.json',encode(screens['all']))
    return audit


def run(request, *, bucket, ca_path, folder, key, model, session):
    """Execute with a fixed cutoff and publish observations independently of analysis status.

    Args:
        request: Validated request payload.
        bucket: Private S3 observation bucket.
        ca_path: Regional RDS trust bundle.
        folder: Temporary container artifact directory.
        key: Secret DeepSeek credential, never logged or persisted.
        model: Server-controlled model identifier.
        session: AWS session backed by the ECS task role.
    """
    # A completed movement can retry delivery without claiming or overwriting its observations.
    if request['kind'] == 'movement':
        with connect_results(ca_path,session=session,cloud=True) as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute("SELECT status,etf_code,analysis_at,data_source FROM movement_analyses WHERE analysis_id=%s",
                               (request['analysis_id'],))
                existing = cursor.fetchone()
            if existing and existing['status'] == 'completed':
                if (existing['etf_code'] != request['etf_code']
                        or existing['analysis_at'] != datetime.fromisoformat(request['analysis_at'])
                        or existing['data_source'] != 'database'):
                    raise ValueError('Completed analysis belongs to a different request')
                count = enqueue_movement(connection, request['analysis_id'])
                LOG.info('Completed movement delivery recovered analysis_id=%s tenants=%s',request['analysis_id'],count)
                return
    folder.mkdir(parents=True,exist_ok=True)
    job = request | {'origin':'cloud','scenario':'database','data_source':'database',
                     'status':'running','started_at':datetime.now(timezone.utc).isoformat()}
    publisher = Publisher(session.client('s3'),bucket,request['analysis_id'],folder)
    # Fail before spending model tokens if observation permissions are missing.
    publisher.publish(job)
    stop = Event()
    def flush():
        while not stop.wait(5):
            try:
                publisher.publish(dict(job))
            except Exception:
                LOG.warning('Observation upload delayed; retrying without stopping analysis')
    thread = Thread(target=flush,daemon=True)
    thread.start()
    failure = None
    try:
        with connect_results(ca_path,session=session,cloud=True) as lock_connection:
            locked = lock_connection.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0))',
                ('cloud:'+request['kind']+':'+request['etf_code'],)).fetchone()[0]
            if not locked:
                raise ValueError('Another analysis of this ETF is running')
            with connect_sources(ca_path,session=session,cloud=True) as connection:
                source = load_prices(connection,load_flow(connection,load_source(connection,request['etf_code'],request['analysis_at'])),request=request)
            source = load_research_observations(lock_connection, source)
            execute_request(kind=request['kind'],source_tools=DatabaseTools(source),
                connection_factory=lambda:connect_results(ca_path,session=session,cloud=True),
                artifacts=folder,analysis_id=request['analysis_id'],key=key,model=model)
            if request['kind'] == 'movement':
                job['delivery_tenants'] = enqueue_movement(lock_connection, request['analysis_id'])
        job['status']='completed'
    except Exception as exc:
        job.update(status='failed',error=type(exc).__name__+': analysis failed; inspect recorded events')
        failure=exc
    finally:
        stop.set()
        thread.join(timeout=90)
        if thread.is_alive():
            raise RuntimeError('Observation upload did not stop')
        job['finished_at']=datetime.now(timezone.utc).isoformat()
        try:
            with connect_results(ca_path,session=session,cloud=True) as connection:
                audit=export_records(connection,request,folder)
            if audit:
                job['contract_status']=audit['status']
            job['observation_status']='complete'
        except Exception:
            job['observation_status']='incomplete'
            job['observation_error']='Database evidence export failed'
            failure=failure or RuntimeError('Evidence export failed')
        publisher.publish(job)
    if failure:
        raise RuntimeError(job.get('error',job.get('observation_error','Cloud execution failed'))) from None


def main():
    """Run the server-controlled task; requests cannot select commands or credentials."""
    parser=argparse.ArgumentParser()
    parser.add_argument('--request',default=os.environ.get('ANALYSIS_REQUEST'))
    args=parser.parse_args()
    request=decode_request(args.request or '',internal=True)
    session=boto3.Session(region_name=os.environ.get('AWS_REGION','ap-northeast-2'))
    secret=json.loads(session.client('secretsmanager').get_secret_value(
        SecretId=os.environ['DEEPSEEK_SECRET_ARN'])['SecretString'])
    key=secret['DEEPSEEK_API_KEY']
    if not isinstance(key,str) or not key:
        raise ValueError('DeepSeek credential unavailable')
    run(request,bucket=os.environ['OBSERVATION_BUCKET'],ca_path=Path(os.environ['RDS_CA_PATH']),
        folder=Path('/tmp/analysis')/request['analysis_id'],key=key,
        model=secret.get('DEEPSEEK_MODEL','deepseek-flash'),session=session)


if __name__=='__main__':
    logging.basicConfig(level=logging.INFO)
    main()
