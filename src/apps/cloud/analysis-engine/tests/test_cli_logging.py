"""CLI 로깅 부트스트랩 테스트 (ALPHA-747).

의도: 상주 소비자는 `ReturnsNotReady` **사유**를 `logger.info` 로 찍는다. 루트 로거
기본은 WARNING 이고 `handler of last resort` 도 WARNING 이라, CLI 가 `basicConfig` 를
걸지 않으면 그 사유가 통째로 삼켜진다 — 08-05 dev 에서 하루치 실패 709건이 로그에
`start` 만 남겨, 네 갈래 사유 중 어느 것인지 알아내려고 ECS run-task 로 트리거를 직접
태워야 했다.

이 회귀를 `caplog` 로는 못 잡는다: `caplog.at_level` 이 테스트 중 로거 수준을 강제해
`basicConfig` 를 지워도 초록이다. 그래서 여기서 **부트스트랩 자체**를 고정한다 —
호출 여부, 수준, 그리고 **dispatch 보다 먼저**인지(뒤로 밀리면 진입 실패가 다시 조용해진다).
"""
from __future__ import annotations

import logging

import pytest

from edge_analysis import cli


def test_cli_configures_info_logging_before_dispatch(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(cli.logging, "basicConfig", lambda **kw: calls.append(kw))
    # dispatch 진입점에서 즉시 중단 — basicConfig 가 이 앞에 있었는지가 판정 대상이다
    def _stop(argv):
        raise SystemExit(7)
    monkeypatch.setattr(cli, "parse_args", _stop)

    with pytest.raises(SystemExit):
        cli.main([])

    assert calls, "CLI 가 로깅을 부트스트랩하지 않는다 — logger.info 사유가 삼켜진다"
    assert calls[0]["level"] == logging.INFO, (
        f"루트 수준이 INFO 가 아니다: {calls[0].get('level')}"
        " — WARNING 이면 ReturnsNotReady 사유가 안 보인다")
    assert calls[0].get("force") is True, (
        "force 없이는 이미 핸들러가 붙은 환경(컨테이너 런타임)에서 설정이 무시된다")
