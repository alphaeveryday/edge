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


def test_single_shot_trigger_yields_to_the_consumer_holding_the_route(monkeypatch):
    """수동 `--trigger-id` 실행도 route 락을 잡는다(ALPHA-779).

    락을 소비자 쪽에만 두면 이 **문서화된 실행 경로**가 그대로 뚫려 있다 — 운영자가
    소비자가 처리 중인 트리거를 다시 태우면 같은 LLM 파이프라인이 둘 돈다. 이중 과금은
    조용해서(둘 다 성공한다) 로그로는 안 드러난다.
    """
    from types import SimpleNamespace

    from edge_analysis.adapters import eventstore

    class HeldStore:
        closed = False

        def try_lock_route(self, route_id):
            assert route_id == eventstore.minute_route_id("mpt_1")
            return False

        def close(self):
            HeldStore.closed = True

    monkeypatch.setattr(cli, "load_settings",
                        lambda **_: SimpleNamespace(lake_bucket="b", deepseek_api_key="k",
                                                    deepseek_model="m"))
    monkeypatch.setattr(cli, "make_s3_client", lambda _s: object())
    monkeypatch.setattr(cli, "LakeReader", lambda _s3, _b: object())
    monkeypatch.setattr(cli, "DeepSeekClient", lambda _k, _m: object())
    monkeypatch.setattr(cli.EventStore, "connect", classmethod(lambda _c, _s: HeldStore()))
    monkeypatch.setattr(cli, "run", lambda *a, **k: pytest.fail("락을 못 잡았는데 파이프라인이 돌았다"))

    assert cli.main(["--trigger-id", "mpt_1"]) == 1, "경합은 비0 으로 드러나야 한다(Rule 12)"
    assert HeldStore.closed, "커넥션을 안 닫으면 다음 실행이 쓸 커넥션이 샌다"
