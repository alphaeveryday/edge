"""Run one explicit real-data analysis and expose artifacts in the existing dashboard."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4

from edge_analysis_v2.analysis.service import execute_request
from edge_analysis_v2.dashboard.jobs import read_settings
from edge_analysis_v2.sources.database import DatabaseTools, connect_sources, load_source, load_flow, load_prices
from edge_analysis_v2.storage.database import connect_results


def main():
    """Read actual observations, close the source connection, then run the agent."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kind', choices=('movement','outlook'), required=True)
    parser.add_argument('--ticker', required=True)
    parser.add_argument('--analysis-at', required=True)
    parser.add_argument('--rds-ca', type=Path, required=True)
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--runs-dir', type=Path, required=True)
    args = parser.parse_args()
    settings = read_settings(args.env_file)
    identity = uuid4().hex
    folder = args.runs_dir/identity
    folder.mkdir(parents=True)
    job = {'analysis_id':identity, 'kind':args.kind, 'scenario':'database', 'data_source':'database',
           'analysis_at':args.analysis_at, 'started_at':datetime.now(timezone.utc).isoformat(),
           'status':'running'}

    def save():
        temporary = folder/'job.tmp'
        temporary.write_text(json.dumps(job,ensure_ascii=False,indent=2),encoding='utf-8')
        temporary.replace(folder/'job.json')

    save()
    print(identity, flush=True)
    try:
        with connect_sources(args.rds_ca) as connection:
            source = load_prices(connection, load_flow(connection, load_source(connection,args.ticker,args.analysis_at)))
        execute_request(kind=args.kind,source_tools=DatabaseTools(source),
            connection_factory=lambda:connect_results(args.rds_ca),artifacts=folder,
            analysis_id=identity, **settings)
        job['status']='completed'
    except Exception as exc:
        job.update(status='failed',error=type(exc).__name__+': '+str(exc)[:1500].replace(settings['key'],'[redacted]'))
        raise
    finally:
        job['finished_at']=datetime.now(timezone.utc).isoformat()
        save()


if __name__ == '__main__':
    main()
