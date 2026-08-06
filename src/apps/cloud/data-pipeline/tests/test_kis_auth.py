"""KIS 인증 테스트 — 토큰 run 당 1회 발급·메모리 캐시, 실패 fail-loud (네트워크 없음)."""

import json

import pytest

from data_pipeline.sources.http import StopFetch
from data_pipeline.sources.kis_auth import KisAuth, domain_for


class FakeClient:
    """POST 토큰 요청을 세는 스텁. body 를 돌려주거나 raise_exc 를 던진다."""

    _sleep = staticmethod(lambda secs: None)

    def __init__(self, body: str = "", raise_exc: Exception | None = None):
        self.body = body
        self.raise_exc = raise_exc
        self.calls = 0

    def request(self, method, url, *, headers=None, data=None, decode=True):
        self.calls += 1
        if self.raise_exc:
            raise self.raise_exc
        return self.body


def _token_body(tok: str = "tok-123") -> str:
    return json.dumps({"access_token": tok, "access_token_token_expired": "2026-07-07 00:00:00"})


def test_token_issued_once_and_cached():
    # WHY: KIS 는 잦은 재발급을 분당 한도로 차단한다 — 여러 번 요청해도 실제 발급은 1회여야
    #      종목마다 토큰을 두드리지 않는다(run 당 1회 규약).
    client = FakeClient(body=_token_body("tok-abc"))
    auth = KisAuth("key", "secret", client, env="prod")

    assert auth.token() == "tok-abc"
    assert auth.token() == "tok-abc"  # 캐시 재사용
    assert client.calls == 1  # 발급은 단 한 번


def test_missing_access_token_fails_loud():
    # WHY: 200 인데 토큰이 없으면(잘못된 grant 등) 조용히 빈 토큰으로 넘기면 이후 전 종목이
    #      401 을 두드린다 — 발급 단계에서 명시적으로 실패해야 한다.
    client = FakeClient(body=json.dumps({"error_description": "invalid grant"}))
    with pytest.raises(RuntimeError):
        KisAuth("key", "secret", client, env="prod").token()


def test_key_error_propagates_as_stop_fetch():
    # WHY: 앱키 오류(4xx)는 client 가 StopFetch 로 올린다 — 재시도·격리 대상이 아니라
    #      즉시 중단이어야 무의미한 호출을 막는다. auth 가 이를 삼키지 않고 전파한다.
    client = FakeClient(raise_exc=StopFetch("HTTP 403"))
    with pytest.raises(StopFetch):
        KisAuth("key", "secret", client, env="prod").token()


def test_unknown_env_fails_loud():
    # WHY: 알 수 없는 env 를 조용히 prod 로 기본화하면 모의/실전을 잘못 친다 — fail loud.
    with pytest.raises(ValueError):
        domain_for("staging")


def test_domain_selects_by_env():
    # WHY: prod/vps 도메인이 뒤바뀌면 실전 키로 모의 서버(또는 반대)를 쳐 데이터가 오염된다.
    assert domain_for("prod").endswith(":9443")
    assert domain_for("vps").endswith(":29443")


def test_토큰_403_은_대기후_1회_재시도한다(monkeypatch):
    # WHY: SFN raw 페이즈의 CollectKisPrice·CollectKisNav 는 같은 앱키를 쓰는 별개 Parallel
    #      브랜치라 거의 동시에 토큰을 발급한다. KIS 는 앱키당 분당 1회만 발급하므로(403
    #      EGW00133, 2026-07-20 실측) 재시도가 없으면 매 런에서 한쪽이 죽어 파이프라인이
    #      상시 partial 이 된다(ALPHA-458). 토큰은 24h 유효라 기다리면 반드시 풀린다.
    from data_pipeline.sources.http import StopFetch
    from data_pipeline.sources.kis_auth import KisAuth, TOKEN_RATE_LIMIT_WAIT_SEC

    slept = []

    class _Client:
        def __init__(self):
            self.calls = 0

        def request(self, method, url, *, headers=None, data=None, decode=True):
            self.calls += 1
            if self.calls == 1:
                raise StopFetch(
                    "HTTP 403: 수집 중단", status=403,
                    body='{"error_code":"EGW00133","error_description":"접근토큰 발급 잠시 후 다시 시도하세요(1분당 1회)"}',
                )
            return json.dumps({"access_token": "TOKEN", "expires_in": 86400})

        def _sleep(self, seconds):
            slept.append(seconds)

    client = _Client()
    auth = KisAuth("k", "s", client)

    assert auth.token() == "TOKEN"
    assert client.calls == 2                            # 대기 후 재시도해 성공
    assert len(slept) == 1
    assert slept[0] >= TOKEN_RATE_LIMIT_WAIT_SEC        # 분당 제한이 풀릴 만큼 기다린다
    assert auth.token() == "TOKEN" and client.calls == 2  # 이후엔 캐시(run 당 1회 규약)


def test_403_이_아닌_4xx_는_기다리지_않고_올린다():
    # WHY: 잘못된 앱키(401 등)는 기다려도 안 풀린다 — 61초를 낭비하고 같은 실패를 반복하는 대신
    #      즉시 드러내야 한다(Rule 12). 유량 제한만 대기 대상이다.
    from data_pipeline.sources.http import StopFetch
    from data_pipeline.sources.kis_auth import KisAuth

    class _Client:
        def request(self, *a, **k):
            raise StopFetch("HTTP 401: 수집 중단", status=401, body="")

        def _sleep(self, seconds):
            raise AssertionError("401 에는 대기하면 안 된다")

    with pytest.raises(StopFetch):
        KisAuth("k", "s", _Client()).token()


def test_재시도_후에도_403_이면_포기한다():
    # WHY: 무한 대기 금지 — 재시도를 다 써도 막혀 있으면 그 런은 실패로 드러내고 스케줄러가 알게 한다.
    from data_pipeline.sources.http import StopFetch
    from data_pipeline.sources.kis_auth import KisAuth, TOKEN_RATE_LIMIT_MAX_RETRY

    class _Client:
        def __init__(self):
            self.calls = 0

        def request(self, *a, **k):
            self.calls += 1
            raise StopFetch("HTTP 403: 수집 중단", status=403, body='{"error_code":"EGW00133","error_description":"접근토큰 발급 잠시 후 다시 시도하세요(1분당 1회)"}')

        def _sleep(self, seconds):
            pass

    client = _Client()
    with pytest.raises(StopFetch):
        KisAuth("k", "s", client).token()
    assert client.calls == TOKEN_RATE_LIMIT_MAX_RETRY + 1  # 소진 후 포기(무한 대기 금지)


def test_유량제한_코드가_아닌_403_은_대기하지_않는다():
    # WHY: 403 이라고 전부 '1분당 1회'가 아니다 — 잘못된 앱키·권한 문제도 4xx 로 오고 그건
    #      기다려도 안 풀린다. 상태코드만 보고 재시도하면 영구 실패를 61초씩 지연시키고
    #      같은 요청을 헛되이 반복한다(edge-review 지적). 코드(EGW00133)로 가른다.
    from data_pipeline.sources.http import StopFetch
    from data_pipeline.sources.kis_auth import KisAuth

    class _Client:
        def request(self, *a, **k):
            raise StopFetch(
                "HTTP 403: 수집 중단", status=403,
                body='{"error_code":"EGW00121","error_description":"유효하지 않은 AppKey"}',
            )

        def _sleep(self, seconds):
            raise AssertionError("유량 제한이 아닌 403 에는 대기하면 안 된다")

    with pytest.raises(StopFetch):
        KisAuth("k", "s", _Client()).token()


def test_대기시간에_지터가_섞여_동시_충돌이_재생산되지_않는다():
    # WHY: 같은 앱키를 쓰는 두 SFN 브랜치가 동시에 403 을 맞으면(직전 1분 내 발급이 있었던
    #      경우 — 빠른 수동 재실행·실행 겹침) 고정 간격 대기는 둘을 같은 시각에 깨워 충돌을
    #      그대로 재생산한다. 지터가 순서를 갈라야 한 쪽이 먼저 발급한다(edge-review 지적).
    from data_pipeline.sources.http import StopFetch
    from data_pipeline.sources.kis_auth import (
        KisAuth, TOKEN_RATE_LIMIT_JITTER_SEC, TOKEN_RATE_LIMIT_WAIT_SEC,
    )

    RATE_BODY = '{"error_code":"EGW00133","error_description":"1분당 1회"}'

    def _waits():
        slept = []

        class _Client:
            def __init__(self):
                self.calls = 0

            def request(self, *a, **k):
                self.calls += 1
                if self.calls == 1:
                    raise StopFetch("HTTP 403", status=403, body=RATE_BODY)
                return json.dumps({"access_token": "T"})

            def _sleep(self, seconds):
                slept.append(seconds)

        KisAuth("k", "s", _Client()).token()
        return slept[0]

    waits = {_waits() for _ in range(20)}
    assert len(waits) > 1, "대기시간이 고정이면 두 브랜치가 같은 시각에 다시 충돌한다"
    assert all(
        TOKEN_RATE_LIMIT_WAIT_SEC <= w <= TOKEN_RATE_LIMIT_WAIT_SEC + TOKEN_RATE_LIMIT_JITTER_SEC
        for w in waits
    )


class ParameterNotFound(Exception):
    """botocore 가 모델에서 만들어 내는 예외와 **같은 클래스 이름** — 코드가 이름으로 가른다."""


class FakeSsm:
    """SSM 스텁 — 파라미터 하나를 메모리에 들고, 원하면 읽기/쓰기에서 터진다."""

    def __init__(self, value: str | None = None, get_raises: Exception | None = None):
        self.value = value
        self.get_raises = get_raises
        self.puts: list[dict] = []

    def get_parameter(self, Name, WithDecryption=False):  # noqa: N803 — boto3 시그니처
        if self.get_raises:
            raise self.get_raises
        if self.value is None:
            raise ParameterNotFound(Name)
        return {"Parameter": {"Value": self.value}}

    def put_parameter(self, **kwargs):
        self.puts.append(kwargs)
        self.value = kwargs["Value"]


def _cache_payload(app_key: str = "k", token: str = "SHARED", remaining: float = 86400.0) -> str:
    import time as _t

    from data_pipeline.sources.kis_auth import _app_key_fingerprint

    return json.dumps(
        {"token": token, "app_key": _app_key_fingerprint(app_key), "expires_at": _t.time() + remaining}
    )


def test_공유캐시가_유효하면_발급하지_않는다():
    # WHY: kis 브랜치 4개가 같은 앱키로 각자 발급하면 분당 1회 제한이 직렬 큐를 만들어 마지막
    #      브랜치가 222초를 기다린다(2026-07-24 실측). 토큰이 24h 유효하다는 사실을 컨테이너
    #      간에 쓰려면 캐시 히트에서 발급 호출이 아예 나가지 않아야 한다.
    from data_pipeline.sources.kis_auth import KisAuth, SsmTokenCache

    client = FakeClient(body=_token_body("ISSUED"))
    cache = SsmTokenCache("/p", FakeSsm(_cache_payload()))

    assert KisAuth("k", "s", client, cache=cache).token() == "SHARED"
    assert client.calls == 0  # 발급이 나가지 않았다


def test_만료_임박_캐시는_재발급하고_SSM_을_갱신한다():
    # WHY: 캐시가 만료 직전인데 그대로 쓰면 런 도중 만료돼 전 종목이 401 을 두드린다. 발급
    #      한 번이 훨씬 싸다. 그리고 새 토큰을 SSM 에 다시 안 쓰면 다음 컨테이너가 같은 만료
    #      임박 값을 또 읽어 모두가 발급하는 현행 상태로 되돌아간다.
    from data_pipeline.sources.kis_auth import CACHE_MIN_REMAINING_SEC, KisAuth, SsmTokenCache

    ssm = FakeSsm(_cache_payload(remaining=CACHE_MIN_REMAINING_SEC - 1))
    client = FakeClient(body=json.dumps({"access_token": "FRESH", "expires_in": 86400}))

    assert KisAuth("k", "s", client, cache=SsmTokenCache("/p", ssm)).token() == "FRESH"
    assert client.calls == 1
    assert len(ssm.puts) == 1
    assert ssm.puts[0]["Type"] == "SecureString"  # 토큰은 평문으로 두지 않는다(ADR-0009)
    assert json.loads(ssm.puts[0]["Value"])["token"] == "FRESH"


def test_다른_앱키로_받은_캐시는_쓰지_않는다():
    # WHY: 앱키를 교체하면 캐시에 남은 옛 키의 토큰은 KIS 가 거부한다. 지문을 안 보면 전 종목이
    #      401 을 맞을 때까지 아무도 모른다 — 미심쩍으면 발급으로 떨어지는 쪽이 안전하다.
    from data_pipeline.sources.kis_auth import KisAuth, SsmTokenCache

    client = FakeClient(body=json.dumps({"access_token": "FRESH", "expires_in": 86400}))
    cache = SsmTokenCache("/p", FakeSsm(_cache_payload(app_key="예전키")))

    assert KisAuth("새키", "s", client, cache=cache).token() == "FRESH"
    assert client.calls == 1


def test_SSM_읽기_실패는_발급_경로로_폴백한다():
    # WHY: 폴백이 이 설계의 핵심이다 — SSM 이 없든 권한이 없든 최악이 '지금처럼 각자 발급'이어야
    #      한다. 캐시 장애가 수집 실패로 번지면 성능 최적화가 가용성 사고가 된다.
    from data_pipeline.sources.kis_auth import KisAuth, SsmTokenCache

    client = FakeClient(body=json.dumps({"access_token": "FRESH", "expires_in": 86400}))
    ssm = FakeSsm(get_raises=RuntimeError("AccessDeniedException"))

    assert KisAuth("k", "s", client, cache=SsmTokenCache("/p", ssm)).token() == "FRESH"
    assert client.calls == 1
    assert len(ssm.puts) == 1  # 쓰기는 계속 시도한다(권한이 읽기만 없을 수도 있다)


def test_SSM_쓰기_실패해도_수집은_계속한다():
    # WHY: 이 런은 이미 토큰을 들고 있다. 공유에 실패했다고 수집을 죽이면 캐시 없이 잘 돌던
    #      경로가 캐시 도입 때문에 깨진다 — 폴백 방향이 반대로 뒤집힌 셈이다.
    from data_pipeline.sources.kis_auth import KisAuth, SsmTokenCache

    class _Ssm(FakeSsm):
        def put_parameter(self, **kwargs):
            raise RuntimeError("ThrottlingException")

    client = FakeClient(body=json.dumps({"access_token": "FRESH", "expires_in": 86400}))
    assert KisAuth("k", "s", client, cache=SsmTokenCache("/p", _Ssm())).token() == "FRESH"


def test_403_직후_캐시를_다시_읽어_대기를_건너뛴다():
    # WHY: 403 을 맞았다는 건 같은 앱키의 다른 컨테이너가 방금 발급했다는 뜻이다 — 그 승자의
    #      토큰이 이미 캐시에 있는데 61초를 기다리면 없애려던 직렬 큐를 그대로 재생산한다.
    #      222초 대기가 사라지는 지점이 정확히 여기다(ALPHA-573).
    from data_pipeline.sources.http import StopFetch
    from data_pipeline.sources.kis_auth import KisAuth, SsmTokenCache

    ssm = FakeSsm()  # 처음엔 비어 있다(ParameterNotFound)

    class _Client:
        def request(self, *a, **k):
            ssm.value = _cache_payload(token="WINNER")  # 다른 컨테이너가 먼저 발급했다
            raise StopFetch(
                "HTTP 403", status=403,
                body='{"error_code":"EGW00133","error_description":"1분당 1회"}',
            )

        def _sleep(self, seconds):
            raise AssertionError("캐시에 공유 토큰이 있으면 기다리면 안 된다")

    assert KisAuth("k", "s", _Client(), cache=SsmTokenCache("/p", ssm)).token() == "WINNER"


@pytest.mark.parametrize(
    "override",
    [
        {"expires_at": float("nan")},
        {"expires_at": float("inf")},
        {"expires_at": 99999999999999},
        {"token": ["T"]},
        {"token": "SECRET\nTOKEN"},
        {"token": ""},
        {"app_key": None},
    ],
)
def test_손상된_캐시값은_히트가_아니라_미스로_떨어진다(override):
    # WHY: 캐시 판정이 틀릴 때 **방향**이 중요하다. NaN 은 모든 비교가 False 라 만료 하한을
    #      그냥 통과하고, inf·터무니없는 미래는 죽은 토큰을 영원히 유효로 만들며, 문자열이
    #      아닌 token 은 `Bearer ['T']` 로 나가 전 종목이 401 을 맞는다. 개행이 든 토큰은
    #      urllib 이 **토큰을 담은** ValueError 를 던져 로그·collection_log 로 유출된다
    #      (ADR-0009). 손상된 값의 실패는 반드시 '한 번 더 발급'(미스) 쪽이어야 한다.
    #      각 케이스는 만료·지문이 정상인 값에서 **한 필드만** 망가뜨린다 — 그래야 다른
    #      게이트에 가려지지 않고 그 필드의 검사가 실제로 일하는지 검증된다.
    import time as _t

    from data_pipeline.sources.kis_auth import KisAuth, SsmTokenCache, _app_key_fingerprint

    valid = {"token": "T", "app_key": _app_key_fingerprint("k"), "expires_at": _t.time() + 86000}
    ssm = FakeSsm(json.dumps(valid | override))
    client = FakeClient(body=json.dumps({"access_token": "FRESH", "expires_in": 86400}))

    assert KisAuth("k", "s", client, cache=SsmTokenCache("/p", ssm)).token() == "FRESH"
    assert client.calls == 1  # 손상된 캐시를 쓰지 않고 발급했다


def test_정상_캐시값은_히트한다():
    # WHY: 위 표가 '전부 미스'로만 통과하면 검사가 과도해 캐시가 아예 안 도는 회귀를 못 잡는다.
    #      같은 base 값이 손상 없이는 히트해야 표의 각 항목이 '그 필드 때문'임이 성립한다.
    import time as _t

    from data_pipeline.sources.kis_auth import KisAuth, SsmTokenCache, _app_key_fingerprint

    valid = {"token": "T", "app_key": _app_key_fingerprint("k"), "expires_at": _t.time() + 86000}
    client = FakeClient(body=json.dumps({"access_token": "FRESH", "expires_in": 86400}))

    assert KisAuth("k", "s", client, cache=SsmTokenCache("/p", FakeSsm(json.dumps(valid)))).token() == "T"
    assert client.calls == 0


def test_403_직후_승자의_쓰기를_짧게_기다린다():
    # WHY: 403 은 '같은 앱키로 방금 누가 발급했다'는 신호지만, 그 승자가 SSM 에 쓰기까지 1초
    #      안팎의 틈이 있다. 한 번만 읽고 포기하면 61초 대기로 떨어져 없애려던 직렬 큐가 그대로
    #      되살아난다 — 이 폴링이 222초 대기를 없애는 지점이다(ALPHA-573).
    from data_pipeline.sources.http import StopFetch
    from data_pipeline.sources.kis_auth import (
        CACHE_RACE_POLL_SEC, TOKEN_RATE_LIMIT_WAIT_SEC, KisAuth, SsmTokenCache,
    )

    ssm = FakeSsm()  # 비어 있다 — 승자가 아직 안 썼다
    slept = []

    class _Client:
        def request(self, *a, **k):
            raise StopFetch(
                "HTTP 403", status=403,
                body='{"error_code":"EGW00133","error_description":"1분당 1회"}',
            )

        def _sleep(self, seconds):
            slept.append(seconds)
            if len(slept) == 2:  # 폴링 두 번째 만에 승자가 캐시에 썼다
                ssm.value = _cache_payload(token="WINNER")

    assert KisAuth("k", "s", _Client(), cache=SsmTokenCache("/p", ssm)).token() == "WINNER"
    assert slept == [CACHE_RACE_POLL_SEC, CACHE_RACE_POLL_SEC]
    assert sum(slept) < TOKEN_RATE_LIMIT_WAIT_SEC  # 1분 대기로 떨어지지 않았다


def test_1분_대기에서_깨어나면_발급_전에_캐시를_먼저_본다():
    # WHY: 폴링이 비었어도 그 사이 다른 컨테이너가 발급해 캐시에 썼을 수 있다. 확인 없이 또
    #      발급하면 '하루 1회 발급 수렴'이 깨지고 분당 1회 큐에 다시 줄을 선다.
    from data_pipeline.sources.http import StopFetch
    from data_pipeline.sources.kis_auth import (
        CACHE_RACE_POLL_ATTEMPTS, TOKEN_RATE_LIMIT_WAIT_SEC, KisAuth, SsmTokenCache,
    )

    ssm = FakeSsm()

    class _Client:
        def __init__(self):
            self.calls = 0

        def request(self, *a, **k):
            self.calls += 1
            raise StopFetch(
                "HTTP 403", status=403,
                body='{"error_code":"EGW00133","error_description":"1분당 1회"}',
            )

        def _sleep(self, seconds):
            if seconds >= TOKEN_RATE_LIMIT_WAIT_SEC:  # 1분 대기 도중 다른 컨테이너가 발급했다
                ssm.value = _cache_payload(token="LATE_WINNER")

    client = _Client()
    assert KisAuth("k", "s", client, cache=SsmTokenCache("/p", ssm)).token() == "LATE_WINNER"
    assert client.calls == 1  # 깨어나서 발급을 다시 두드리지 않았다
    assert CACHE_RACE_POLL_ATTEMPTS > 0


def test_캐시가_죽어_있으면_승자_폴링을_하지_않는다():
    # WHY: 이 설계의 전제는 "캐시가 실패해도 최악이 현행 동작"이다. 권한 없음·SSM 장애처럼
    #      읽기 자체가 실패하는 환경에서 403 재시도마다 헛폴링(2초×5)을 반복하면 폴백 경로가
    #      현행보다 40초 넘게 **느려져** 전제가 뒤집힌다(edge-review 지적).
    from data_pipeline.sources.http import StopFetch
    from data_pipeline.sources.kis_auth import (
        CACHE_RACE_POLL_SEC, TOKEN_RATE_LIMIT_MAX_RETRY, TOKEN_RATE_LIMIT_WAIT_SEC,
        KisAuth, SsmTokenCache,
    )

    slept = []

    class _Client:
        def request(self, *a, **k):
            raise StopFetch(
                "HTTP 403", status=403,
                body='{"error_code":"EGW00133","error_description":"1분당 1회"}',
            )

        def _sleep(self, seconds):
            slept.append(seconds)

    cache = SsmTokenCache("/p", FakeSsm(get_raises=RuntimeError("AccessDeniedException")))
    with pytest.raises(StopFetch):
        KisAuth("k", "s", _Client(), cache=cache).token()

    assert CACHE_RACE_POLL_SEC not in slept  # 헛폴링이 한 번도 없었다
    assert len(slept) == TOKEN_RATE_LIMIT_MAX_RETRY  # 대기는 현행과 똑같이 재시도 예산만큼
    assert all(s >= TOKEN_RATE_LIMIT_WAIT_SEC for s in slept)


def test_폴링_창_끝에_들어온_쓰기도_잡는다():
    # WHY: 마지막 잠 뒤에 확인 없이 포기하면, 승자가 창 끝(8~10초)에 쓴 값을 눈앞에 두고
    #      61초를 더 기다린다 — 폴링 창을 선언한 만큼 실제로 관찰해야 한다(edge-review 지적).
    from data_pipeline.sources.http import StopFetch
    from data_pipeline.sources.kis_auth import CACHE_RACE_POLL_ATTEMPTS, KisAuth, SsmTokenCache

    ssm = FakeSsm()
    sleeps = []

    class _Client:
        def request(self, *a, **k):
            raise StopFetch(
                "HTTP 403", status=403,
                body='{"error_code":"EGW00133","error_description":"1분당 1회"}',
            )

        def _sleep(self, seconds):
            sleeps.append(seconds)
            if len(sleeps) == CACHE_RACE_POLL_ATTEMPTS:  # 창의 마지막 잠 도중에 들어온 쓰기
                ssm.value = _cache_payload(token="LAST_MOMENT")

    assert KisAuth("k", "s", _Client(), cache=SsmTokenCache("/p", ssm)).token() == "LAST_MOMENT"
    assert len(sleeps) == CACHE_RACE_POLL_ATTEMPTS  # 1분 대기까지 가지 않았다


def test_승자_폴링은_첫_403_에서만_한다():
    # WHY: 경합은 컨테이너들이 동시에 시작하는 그 순간에만 있다. 아무도 캐시를 못 쓰는 상황
    #      (승자가 발급엔 성공했지만 쓰기 권한이 없어 파라미터가 계속 없는 경우)에서 재시도마다
    #      폴링하면 10초씩 얹혀 폴백이 현행보다 느려진다 — "최악이 현행 동작" 전제가 깨진다
    #      (edge-review 지적).
    from data_pipeline.sources.http import StopFetch
    from data_pipeline.sources.kis_auth import CACHE_RACE_POLL_ATTEMPTS, CACHE_RACE_POLL_SEC, KisAuth, SsmTokenCache

    slept = []

    class _Client:
        def request(self, *a, **k):
            raise StopFetch(
                "HTTP 403", status=403,
                body='{"error_code":"EGW00133","error_description":"1분당 1회"}',
            )

        def _sleep(self, seconds):
            slept.append(seconds)

    with pytest.raises(StopFetch):
        KisAuth("k", "s", _Client(), cache=SsmTokenCache("/p", FakeSsm())).token()

    # 폴링 잠은 첫 403 에서 쓴 만큼만 — 재시도마다 반복되지 않는다.
    assert slept.count(CACHE_RACE_POLL_SEC) == CACHE_RACE_POLL_ATTEMPTS


def test_만료를_못_읽은_토큰은_캐시하지_않는다():
    # WHY: 만료 시각을 모르는 토큰을 기본값으로 캐시하면 이미 죽은 토큰을 유효하다고 우기는
    #      쪽으로 틀린다(전 종목 401). 못 읽으면 이번 런에서만 쓰고 버리는 게 안전한 방향이다.
    from data_pipeline.sources.kis_auth import KisAuth, SsmTokenCache

    ssm = FakeSsm()
    client = FakeClient(body=json.dumps({"access_token": "FRESH"}))  # expires_in 없음

    assert KisAuth("k", "s", client, cache=SsmTokenCache("/p", ssm)).token() == "FRESH"
    assert ssm.puts == []


def test_캐시_파라미터_env_가_없으면_캐시를_안_쓴다(monkeypatch):
    # WHY: 로컬 실행·단위테스트는 AWS 없이 돌아야 한다(레이크 s3 백엔드와 같은 관례). env 가
    #      없을 때 boto3 를 건드리면 캐시와 무관한 실행이 전부 깨진다.
    from data_pipeline.sources.kis_auth import CACHE_PARAM_ENV, KisAuth

    monkeypatch.delenv(CACHE_PARAM_ENV, raising=False)
    auth = KisAuth("k", "s", FakeClient(body=_token_body("tok")))
    assert auth.cache is None
    assert auth.token() == "tok"


def test_재시도_예산이_동시_kis_브랜치_수보다_크다():
    # WHY: 토큰 발급이 분당 1회라 N개 브랜치가 동시에 시작하면 마지막 브랜치는 N-1 분을
    #      기다려야 한다. 예산이 브랜치 수보다 작으면 그 브랜치는 상시 partial 이 된다
    #      (edge-review 지적 — kis 브랜치가 2개에서 3개로 늘었는데 예산이 2였다).
    #      SFN 의 kis 브랜치 수와 이 상수가 같이 움직여야 한다는 계약을 값으로 고정한다.
    #
    # ⚠️ **세는 단위는 잡 정의가 아니라 레인이다**(ALPHA-769). 동시 발급은 한 SFN 의 Parallel
    #    안에서만 일어나는데, `statemachine.tf` 의 잡 리스트에는 그 SFN 이 돌지 않는 정의도
    #    들어 있다 — 레인 파일들이 같은 리스트를 부분집합 필터로 재사용하기 때문이다(DRY).
    #    종전 파서는 파일 전체의 `taskdef_key = "kis"` 를 셌고, 그때까지 제외된 잡 중 kis 가
    #    하나도 없어서 두 수가 우연히 같았다. 장중 수급 레인이 kis 잡을 시장 SFN 밖으로
    #    가져가면서 갈렸다 — 그대로 두면 **동시에 뜨지도 않는 브랜치 때문에 예산을 올리게 된다**.
    import pathlib as _p
    import re as _re

    from data_pipeline.sources.kis_auth import TOKEN_RATE_LIMIT_MAX_RETRY

    here = _p.Path(__file__).resolve()
    tf = next(
        (parent / "infra/terraform/modules/data-pipeline/statemachine.tf"
         for parent in here.parents
         if (parent / "infra/terraform/modules/data-pipeline/statemachine.tf").exists()),
        None,
    )
    if tf is None:  # 저장소 밖(패키지만 설치된 환경)에서는 검사할 대상이 없다
        pytest.skip("statemachine.tf 를 찾을 수 없음 — 저장소 체크아웃에서만 도는 계약 검사")
    text = tf.read_text()
    kis_states = _re.findall(r'state\s*=\s*"(\w+)"\s*\n\s*taskdef_key\s*=\s*"kis"', text)
    assert len(kis_states) >= 3, f"kis 잡 파싱 실패({kis_states}) — 경로·형식 확인"

    excluded_block = _re.search(
        r"market_excluded_states\s*=\s*\[(.*?)\]", text, _re.S)
    assert excluded_block, "market_excluded_states 를 못 찾았다 — 파서가 낡았다"
    excluded = set(_re.findall(r'"(\w+)"', excluded_block.group(1)))

    # 레인별 kis 잡 수. 시장 = 제외되지 않은 것, 나머지는 각 레인 파일이 이름으로 고른 것.
    per_lane = {"market": [s for s in kis_states if s not in excluded]}
    for lane_file in ("news_pipeline.tf", "disclosure_pipeline.tf",
                      "investor_intraday_pipeline.tf"):
        lane_text = (tf.parent / lane_file).read_text()
        per_lane[lane_file] = [
            s for s in kis_states if s in excluded and f'"{s}"' in lane_text
        ]
    orphans = [s for s in kis_states
               if s in excluded and not any(s in v for v in per_lane.values())]
    assert not orphans, f"시장에서 뺐는데 어느 레인도 안 가진 kis 잡: {orphans}"

    concurrent = max(len(v) for v in per_lane.values())
    assert TOKEN_RATE_LIMIT_MAX_RETRY > concurrent - 1, (
        f"동시 발급자 {concurrent}개(레인별 {per_lane})인데 재시도 예산이 "
        f"{TOKEN_RATE_LIMIT_MAX_RETRY} — 마지막 브랜치가 자기 차례를 못 받는다"
    )
