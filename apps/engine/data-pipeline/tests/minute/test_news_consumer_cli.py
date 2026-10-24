"""뉴스 추출 Consumer 진입점 테스트 (ALPHA-713).

의도: 이 CLI 의 결함은 두 모양으로 조용하다 — ① 배선 결손(DB·큐·LLM 키)을 안고 뜨면
handler 가 job 마다 llm_error 재시도를 돌다 예산 소진으로 DEAD 를 쌓고, ② kind/큐가
어긋나면 커널이 전건 misrouted 로 접는데 프로세스는 살아 있다. 그래서 고정하는 건
**기동에서 죽는 것**(결손별 SystemExit)과 **커널에 넘기는 배선 값**(kind=news·큐 URL)이다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from data_pipeline.config import DbConfig, StorageConfig
from data_pipeline.config.models import MinuteNewsConsumerConfig
from data_pipeline.minute import news_consumer
from data_pipeline.minute.news_consumer import news_consumer_cli


class FakeSettings:
    def __init__(self, *, db=True, options=True):
        self.db = DbConfig(password="x") if db else None
        self.minute_news_consumer = (
            MinuteNewsConsumerConfig(queue_url="https://sqs/news-rt") if options else None
        )
        self.storage = StorageConfig()


def test_missing_db_fails_loud(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "k")
    with pytest.raises(SystemExit):
        news_consumer_cli(FakeSettings(db=False))


def test_missing_config_fails_loud(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "k")
    with pytest.raises(SystemExit):
        news_consumer_cli(FakeSettings(options=False))


def test_missing_llm_key_fails_loud(monkeypatch):
    """키 없이 뜨면 job 마다 llm_error 재시도가 예산을 태워 DEAD 를 쌓는다 — 기동에서 죽는다."""
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        news_consumer_cli(FakeSettings())


def test_wires_news_kind_and_queue(monkeypatch):
    """커널에 kind=news·설정의 큐 URL 이 그대로 간다 — price 복붙에서 틀리기 쉬운 두 축이다."""
    monkeypatch.setenv("LLM_API_KEY", "k")
    built = {}

    class FakeConsumer:
        def __init__(self, *, jobs, queue, handler, config):
            built["config"] = config
            built["handler"] = handler

        def request_stop(self):
            pass

        def tick(self, now):
            return {"stopped": 1}  # 첫 tick 에 정지 신호 — 루프를 즉시 끝낸다

        def close(self):
            built["closed"] = True

    from data_pipeline.minute import consumer as kernel
    monkeypatch.setattr(kernel, "MinuteConsumer", FakeConsumer)

    rc = news_consumer_cli(FakeSettings(), max_ticks=1)

    assert rc == 1, "bounded 실행이 확인을 못 끝냈으면(stopped) 성공으로 접지 않는다"
    assert built["config"].kind == "news"
    assert built["config"].queue_url == "https://sqs/news-rt"
    assert isinstance(built["handler"], news_consumer.NewsExtractionHandler)
    assert built.get("closed"), "close() 없이 끝나면 in-flight 기록이 유실된다"


def test_blank_llm_key_fails_loud(monkeypatch):
    """공백 키는 기동되고 job 마다 잘못된 인증으로 예산을 태운다 — 결손과 같게 취급한다."""
    monkeypatch.setenv("LLM_API_KEY", "   ")
    with pytest.raises(SystemExit):
        news_consumer_cli(FakeSettings())
