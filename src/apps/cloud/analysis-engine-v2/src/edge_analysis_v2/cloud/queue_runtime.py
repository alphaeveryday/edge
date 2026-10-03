"""Run the existing price queue as an admission service, without calling the model."""
import logging
import os
from pathlib import Path
import signal
from threading import Event

import boto3
from botocore.config import Config

from edge_analysis_v2.cloud.admission import WorkflowAdmission
from edge_analysis_v2.cloud.consumer import serve
from edge_analysis_v2.sources.database import connect_sources
from edge_analysis_v2.sources.triggers import load_queue_event
from edge_analysis_v2.storage.database import connect_results
from edge_analysis_v2.storage.requests import RequestStore
from edge_analysis_v2.storage.retractions import retract_movement


def main():
    """Connect minimal AWS/DB clients and stop polling when ECS asks us to exit."""
    session = boto3.Session(region_name=os.environ['AWS_REGION'])
    ca = Path(os.environ['RDS_CA_PATH'])
    stop = Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())

    def results():
        return connect_results(ca, session=session, cloud=True)

    def load(raw):
        with connect_sources(ca, session=session, cloud=True) as connection:
            return load_queue_event(connection, raw)

    def retract(event):
        with results() as connection:
            return retract_movement(connection, event)

    config = Config(connect_timeout=5, read_timeout=30, retries={'mode':'standard','max_attempts':3})
    admission = WorkflowAdmission(session.client('stepfunctions', config=config),
        os.environ['STATE_MACHINE_ARN'], requests=RequestStore(results))
    serve(session.client('sqs', config=config), os.environ['PRICE_QUEUE_URL'], load, admission, stop, retract)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    main()
