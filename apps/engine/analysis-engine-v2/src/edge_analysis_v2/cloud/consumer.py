"""SQS transport for durable price-event admission, separate from analysis work."""
import logging

LOG = logging.getLogger(__name__)


def poll_once(sqs, queue_url, load_request, admission, stop, retract=None):
    """Admit one received event before deleting its current receipt.

    Args:
        sqs: SQS client limited to the existing explanation queue.
        queue_url: Server-configured queue URL.
        load_request: Resolves the original body against the source database.
        admission: WorkflowAdmission with a durable request store.
        stop: Shutdown event; prevents starting work after a long poll returns.

    Raises:
        ValueError: Durable storage is missing or the source rejects the event.
        Exception: Dependency failure; the receipt remains eligible for redelivery.
    """
    if admission.requests is None:
        raise ValueError('Queue admission requires durable request storage')
    response = sqs.receive_message(QueueUrl=queue_url,MaxNumberOfMessages=1,WaitTimeSeconds=20)
    if stop.is_set():
        return
    for message in response.get('Messages',[]):
        receipt = message['ReceiptHandle']
        request = load_request(message['Body'])
        if request.get('event_type') == 'ExposureReverted':
            if retract is None:
                raise ValueError('Reversion handler is required')
            retract(request)
            sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt)
            LOG.info('Price reversion delivered event_id=%s', request['event_id'])
            continue
        if request.get('kind')!='movement' or 'source' not in request:
            raise ValueError('Queue admission requires an original price event')
        admission.submit(request)
        sqs.delete_message(QueueUrl=queue_url,ReceiptHandle=receipt)
        LOG.info('Price event admitted analysis_id=%s',request['analysis_id'])


def serve(sqs, queue_url, load_request, admission, stop, retract=None):
    """Poll until shutdown, retaining failed receipts for SQS retry and DLQ.

    Args:
        sqs: SQS client with bounded connection/read timeouts.
        queue_url: Server-configured queue URL.
        load_request: Source resolver that closes its connection before returning.
        admission: Durable workflow admission; never executes the model here.
        stop: Event set by the runtime on SIGTERM or SIGINT.
    """
    if admission.requests is None:
        raise ValueError('Queue admission requires durable request storage')
    while not stop.is_set():
        try:
            poll_once(sqs,queue_url,load_request,admission,stop,retract)
        except Exception as exc:
            # Bodies, receipt handles and dependency exception text may contain secrets.
            LOG.warning('Price admission failed type=%s; acknowledgement unconfirmed',type(exc).__name__)
            stop.wait(5)
