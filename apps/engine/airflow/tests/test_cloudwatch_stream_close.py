"""태스크 로그 업로드 뒤 그 CloudWatch 스트림의 watchtower 스레드가 끝난다 (config/airflow_local_settings.py, ALPHA-1363).

watchtower 는 스트림 이름마다 큐+데몬 스레드를 만들고 ``close()`` 에서만 끝내는데, provider 의 ``CloudWatchRemoteLogIO`` 는
워커 수명 동안 핸들러 하나를 공유하며 ``upload()`` 에서 flush 만 한다 — 태스크 시도마다 스레드가 남아 워커 RSS 가 하루
≈11 MiB 씩 늘었다(dev 실측). 패치가 지켜야 할 계약은 셋이다: ① 업로드한 스트림의 큐·스레드는 사라진다, ② 그 스트림에
쌓였던 로그는 버려지지 않고 전송된다, ③ 핸들러는 살아 있어 다음 시도의 로그가 그대로 흐른다(핸들러를 통째로 닫으면 ③이
깨진다 — provider 가 flush 만 하는 이유). 가짜 logs 클라이언트를 쓰므로 실제 AWS 는 부르지 않는다.
"""
import logging
import sys
import threading
import time
from pathlib import Path

import watchtower
from airflow.providers.amazon.aws.log.cloudwatch_task_handler import CloudWatchRemoteLogIO

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "config"))
import airflow_local_settings  # noqa: E402,F401  — import 가 곧 패치 적용(이미지에선 `import airflow` 가 읽는다)

# processors() 는 로그 상대 경로의 ':' 를 '_' 로 바꿔 스트림 이름을 짓는다. upload() 는 바꾸기 전 경로를 받는다.
LOG_PATH_1 = "dag_id=edge_source_daily/run_id=scheduled__2026-10-19T00:00:00+00:00/task_id=plan/attempt=1.log"
LOG_PATH_2 = LOG_PATH_1.replace("attempt=1", "attempt=2")
STREAM_1, STREAM_2 = LOG_PATH_1.replace(":", "_"), LOG_PATH_2.replace(":", "_")


class FakeLogs:
    """boto3 logs 클라이언트 대역 — 보낸 메시지를 스트림별로 모은다."""

    class exceptions:
        class ResourceAlreadyExistsException(Exception): ...
        class ResourceNotFoundException(Exception): ...
        class DataAlreadyAcceptedException(Exception): ...
        class InvalidSequenceTokenException(Exception): ...

    def __init__(self):
        self.sent: dict[str, list[str]] = {}

    def create_log_stream(self, **_):
        return {}

    def put_log_events(self, logStreamName, logEvents, **_):
        self.sent.setdefault(logStreamName, []).extend(e["message"] for e in logEvents)
        return {"nextSequenceToken": "t"}


def make_io(tmp_path):
    io = CloudWatchRemoteLogIO(
        base_log_folder=tmp_path, delete_local_copy=False,
        log_group_arn="arn:aws:logs:ap-northeast-2:000000000000:log-group:/airflow/test/tasks",
    )
    io._cached_handler = watchtower.CloudWatchLogHandler(
        log_group_name="/airflow/test/tasks", log_stream_name="", use_queues=True,
        boto3_client=FakeLogs(), create_log_group=False,
    )
    return io


def emit(io, stream, message):
    handler = io.handler
    handler.log_stream_name = stream
    handler.handle(logging.LogRecord("airflow.task", logging.INFO, "", 0, message, (), None))


def wait_until(cond, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not cond() and time.monotonic() < deadline:
        time.sleep(0.05)
    return cond()


def test_upload_ends_that_stream_delivers_its_logs_and_keeps_handler_live(tmp_path):
    io = make_io(tmp_path)
    handler, client = io.handler, io.handler.cwl_client
    threads_before = threading.active_count()

    emit(io, STREAM_1, "attempt 1 line")
    assert STREAM_1 in handler.queues and threading.active_count() == threads_before + 1

    io.upload(LOG_PATH_1)

    assert STREAM_1 not in handler.queues  # ① 큐가 등록부에서 빠졌다
    assert wait_until(lambda: threading.active_count() == threads_before), "스트림 스레드가 끝나지 않았다"
    assert client.sent[STREAM_1] == ["attempt 1 line"]  # ② END 가 쌓인 로그를 버리지 않았다
    assert not handler.shutting_down  # ③ 핸들러는 살아 있다

    emit(io, STREAM_2, "attempt 2 line")  # 같은 워커의 다음 시도
    handler.flush()
    assert client.sent[STREAM_2] == ["attempt 2 line"] and STREAM_2 in handler.queues
    io.upload(LOG_PATH_2)
    assert handler.queues == {} and wait_until(lambda: threading.active_count() == threads_before)
    assert wait_until(lambda: not any(t.is_alive() for t in handler.threads))


def test_upload_of_stream_never_logged_is_a_noop(tmp_path):
    io = make_io(tmp_path)
    threads_before = threading.active_count()
    io.upload("dag_id=x/run_id=y/task_id=z/attempt=1.log")
    assert io.handler.queues == {} and threading.active_count() == threads_before
