"""KIS 토큰 파일 캐시 — AWS 가 없는 환경(OCI VM)에서 SSM 캐시를 대신한다.

KIS 는 토큰 발급을 앱키당 분당 1회로 막고, 발급할 때마다 계좌 주인에게 알림을 보낸다. 그래서
여기서 고정하는 계약은 둘이다: env 로 파일 캐시를 고르면 **AWS 를 한 번도 부르지 않고**, 같은
디렉터리를 보는 프로세스들은 동시에 미스를 봐도 **한 번만 발급한다**.
"""

import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from data_pipeline.sources import kis_auth
from data_pipeline.sources.kis_auth import FileTokenCache, KisAuth


class IssuingClient:
    """발급(POST)마다 횟수를 세고 앱키별 토큰을 낸다."""

    def __init__(self):
        self.issued = 0

    def request(self, method, url, *, headers=None, data=None, decode=True):
        self.issued += 1
        return json.dumps({"access_token": "T-" + json.loads(data)["appkey"], "expires_in": 86400})

    @staticmethod
    def _sleep(seconds):
        pass


@pytest.fixture
def file_cache_env(tmp_path, monkeypatch):
    aws_calls = []

    def no_aws():
        aws_calls.append("ssm")  # 캐시가 예외를 미스로 삼키므로 호출 자체를 센다
        raise AssertionError("파일 캐시를 골랐는데 SSM 을 불렀다")

    monkeypatch.setenv("KIS_TOKEN_CACHE_DIR", str(tmp_path))
    # AWS 에서 넘어온 env 가 남아 있어도 파일 캐시가 이겨야 한다.
    monkeypatch.setenv("KIS_TOKEN_CACHE_PARAM", "/edge-dev-data-pipeline/kis/access-token")
    monkeypatch.setattr(kis_auth, "ssm_client", no_aws)
    yield tmp_path
    assert aws_calls == []


def test_env_로_고른_파일_캐시는_AWS_없이_프로세스_사이에_토큰을_넘긴다(file_cache_env):
    # WHY: OCI VM 에는 SSM 이 없다. env 를 무시하고 SSM 으로 가면 캐시가 매번 실패해 스텝마다
    #      발급하고, 같은 분에 뜬 스텝은 403 으로 61초씩 기다린다.
    client = IssuingClient()

    assert KisAuth("k", "s", client).token() == "T-k"
    assert KisAuth("k", "s", client).token() == "T-k"  # 새 프로세스를 흉내 낸 새 인스턴스

    assert client.issued == 1
    (cached,) = file_cache_env.glob("kis-token-*.json")
    assert stat.S_IMODE(cached.stat().st_mode) == 0o600  # bearer 자격증명이다


def test_앱키_둘은_파일이_갈려_서로의_토큰을_밀어내지_않는다(file_cache_env):
    # WHY: 앱키가 둘이다(kis/oauth · kis/oauth-2). 한 파일을 나눠 쓰면 지문 불일치로 서로를
    #      미스로 만들어, 프로세스가 뜰 때마다 번갈아 재발급한다.
    client = IssuingClient()

    assert KisAuth("k1", "s", client).token() == "T-k1"
    assert KisAuth("k2", "s", client).token() == "T-k2"
    assert KisAuth("k1", "s", client).token() == "T-k1"

    assert client.issued == 2


def test_캐시_디렉터리가_없으면_발급으로_떨어지고_설정_오류를_드러낸다(tmp_path, caplog):
    # WHY: 마운트가 빠져도 수집은 돌아야 한다 — 최악이 캐시 없던 시절의 동작(각자 발급)이다.
    #      다만 '아직 아무도 안 썼다'와 같은 정상 미스로 치면 설정 오류가 INFO 한 줄에 묻히고,
    #      403 을 맞았을 때 아무도 못 쓰는 캐시를 10초 헛폴링한다 — 죽은 캐시로 판정돼야 한다.
    client = IssuingClient()
    cache = FileTokenCache(str(tmp_path / "없음"))

    assert KisAuth("k", "s", client, cache=cache).token() == "T-k"
    assert client.issued == 1
    assert cache.read_failed is True
    assert [record.levelname for record in caplog.records].count("ERROR") == 1


def test_아직_아무도_안_쓴_디렉터리는_정상_미스다(tmp_path):
    # WHY: 그날 첫 프로세스는 늘 빈 디렉터리를 본다. 이걸 죽은 캐시로 치면 403 뒤 승자 폴링이
    #      가장 필요한 순간에 꺼진다.
    cache = FileTokenCache(str(tmp_path))

    assert cache.get("지문") is None
    assert cache.read_failed is False


def test_깨진_캐시_파일은_경고를_남기고_새_발급이_덮어쓴다(tmp_path, caplog):
    # WHY: 못 읽는 파일(권한·손상)을 조용히 미스로 넘기면 프로세스가 뜰 때마다 재발급하는 상태가
    #      INFO 로그에 묻힌다. 경고로 드러내고, 다음 발급이 파일을 고쳐 놓아야 한다.
    client = IssuingClient()
    (broken,) = [tmp_path / f"kis-token-{kis_auth._app_key_fingerprint('k')}.json"]
    broken.write_text("{잘린 값", encoding="utf-8")

    assert KisAuth("k", "s", client, cache=FileTokenCache(str(tmp_path))).token() == "T-k"
    assert "WARNING" in [record.levelname for record in caplog.records]
    assert KisAuth("k", "s", client, cache=FileTokenCache(str(tmp_path))).token() == "T-k"
    assert client.issued == 1


def test_파일_캐시는_403_뒤에_승자를_폴링하지_않는다(tmp_path):
    # WHY: 이 디렉터리에 쓰는 프로세스는 모두 같은 잠금을 거친다. 잠금을 쥔 채 승자의 쓰기를
    #      기다려도 아무도 못 쓰니, 폴링 10초는 나와 잠금 대기자 모두를 그만큼 늦출 뿐이다.
    from data_pipeline.sources.http import StopFetch

    slept = []

    class RateLimitedOnce(IssuingClient):
        def request(self, method, url, *, headers=None, data=None, decode=True):
            if not slept:
                raise StopFetch("HTTP 403", status=403, body='{"error_code":"EGW00133"}')
            return super().request(method, url, headers=headers, data=data, decode=decode)

        def _sleep(self, seconds):
            slept.append(seconds)

    assert KisAuth("k", "s", RateLimitedOnce(), cache=FileTokenCache(str(tmp_path))).token() == "T-k"
    assert len(slept) == 1 and slept[0] >= kis_auth.TOKEN_RATE_LIMIT_WAIT_SEC


# 자식 프로세스: 준비를 알리고, 출발 신호를 기다렸다가 토큰을 받는다. 발급은 로그에 한 줄씩 남긴다.
CHILD = """
import json, os, sys, time
from data_pipeline.sources.kis_auth import FileTokenCache, KisAuth

directory, go, issue_log = sys.argv[1:]


class SlowClient:
    def request(self, method, url, *, headers=None, data=None, decode=True):
        with open(issue_log, "a", encoding="utf-8") as handle:
            handle.write("issued\\n")
        time.sleep(0.5)  # 발급 응답을 기다리는 동안이 경합 창이다
        return json.dumps({"access_token": "T", "expires_in": 86400})

    @staticmethod
    def _sleep(seconds):
        pass


open(f"{go}.ready-{os.getpid()}", "w").close()
while not os.path.exists(go):
    time.sleep(0.01)
sys.exit(0 if KisAuth("k", "s", SlowClient(), cache=FileTokenCache(directory)).token() == "T" else 1)
"""


def test_두_프로세스가_동시에_미스를_봐도_발급은_한_번이다(tmp_path):
    # WHY: 같은 VM 에서 1분 워커 여럿이 07:50 에 함께 뜬다. 각자 발급하면 한쪽이 403 을 맞아
    #      61초를 기다리고(그 동안 1분 window 가 결손), 계좌 주인에게 발급 알림이 두 번 간다.
    cache_dir, go, issue_log = tmp_path / "cache", tmp_path / "go", tmp_path / "issued.log"
    cache_dir.mkdir()
    env = {**os.environ, "PYTHONPATH": str(Path(kis_auth.__file__).parents[2])}
    children = [
        subprocess.Popen([sys.executable, "-c", CHILD, str(cache_dir), str(go), str(issue_log)], env=env)
        for _ in range(2)
    ]
    try:
        deadline = time.monotonic() + 30
        while len(list(tmp_path.glob("go.ready-*"))) < 2:  # 둘 다 import 를 끝낸 뒤 함께 출발시킨다
            assert time.monotonic() < deadline and all(child.poll() is None for child in children)
            time.sleep(0.01)
        go.touch()
        codes = [child.wait(timeout=30) for child in children]
    finally:
        for child in children:
            child.kill()

    assert codes == [0, 0]  # 둘 다 같은 토큰을 받았다
    assert issue_log.read_text(encoding="utf-8").count("issued") == 1
