"""태스크 로그 업로드가 끝난 CloudWatch 스트림의 watchtower 큐·스레드를 끝낸다 (ALPHA-1363).

Airflow 는 모든 프로세스에서 ``import airflow`` 때 ``$AIRFLOW_HOME/config/airflow_local_settings.py`` 를 읽는다
(settings.initialize → import_local_settings). 그래서 이 파일은 이미지에 COPY 만 하면 LocalExecutor 워커에도 들어간다.

왜 필요한가 — watchtower 3.4.0 ``CloudWatchLogHandler(use_queues=True)`` 는 로그 스트림 이름마다 큐 하나와 데몬 스레드
하나를 만들어 ``handler.queues``·``handler.threads`` 에 넣고, ``close()`` 에서만 END 를 보내 끝낸다. amazon provider 9.36.0
``CloudWatchRemoteLogIO`` 는 워커 프로세스 수명 동안 핸들러 하나를 공유하고(뒤 태스크의 로그를 버리지 않으려고) ``upload()``
에서 ``flush()`` 만 하므로, 태스크 시도(=스트림) 하나마다 스레드·큐가 영구히 남는다. dev 실측: 워커 스레드 수 = 재기동 뒤
태스크 수, 태스크당 RSS 0.27 MiB, 평일 ≈11 MiB — 2주마다 task_memory 90% 정지 알람에 닿는 증가의 원인.

무엇을 하나 — 업로드가 끝난 스트림은 다시 쓰이지 않는다(시도마다 ``attempt=N`` 이 다른 새 이름). 그 스트림의 큐에만 END 를
넣어 스레드를 끝내고 등록부에서 지운다. 핸들러 자체는 닫지 않으므로 다음 태스크의 로그는 그대로 흐른다.
"""
from __future__ import annotations

import os

from airflow.providers.amazon.aws.log import cloudwatch_task_handler as _cloudwatch

_upload = _cloudwatch.CloudWatchRemoteLogIO.upload


def _upload_then_end_stream(self: _cloudwatch.CloudWatchRemoteLogIO, path: os.PathLike | str, ti=None) -> None:
    _upload(self, path, ti)  # flush(모든 큐가 빌 때까지 대기) + 로컬 사본 삭제
    handler = self._cached_handler
    if handler is None or handler.shutting_down:
        return
    # processors() 가 짓는 스트림 이름과 같은 규칙: 상대 경로(posix) 의 ':' 를 '_' 로.
    stream_name = str(path).replace(":", "_")
    pending = handler.queues.pop(stream_name, None)
    if pending is None:
        return
    pending.put(handler.END)
    with pending.all_tasks_done:
        pending.all_tasks_done.wait_for(lambda: pending.unfinished_tasks == 0, timeout=handler.FLUSH_TIMEOUT)
    # 끝난 스레드 객체를 등록부에서 뺀다. 방금 END 를 받은 스레드는 task_done 직후 한두 명령 뒤에 끝나므로
    # 이번 호출에서 아직 살아 있을 수 있다 — 그 객체는 다음 업로드 때 빠진다(OS 스레드는 이미 끝났다).
    handler.threads[:] = [t for t in handler.threads if t.is_alive()]


_cloudwatch.CloudWatchRemoteLogIO.upload = _upload_then_end_stream  # type: ignore[method-assign]
