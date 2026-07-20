"""KRX 정보데이터시스템 로그인 — run 당 1회 로그인·세션 쿠키(JSESSIONID) 캐시.

ETF PDF 구성종목(MDCSTAT05001)은 KRX 계정 로그인 게이트 뒤에 있어(무료 OHLCV/NAV 는
무로그인 가능, PDF 비중 스냅샷만 계정 게이트), getJsonData 호출 전에 KRX 계정으로 로그인해
승격된 JSESSIONID 세션 쿠키를 얻어야 한다. KIS(kis_auth)가 run 당 1회 토큰을 발급해
재사용하듯, 여기선 run 당 1회 로그인해 JSESSIONID 를 재사용한다(session() → 쿠키 문자열).

로그인은 2단계(라이브 실측): (1) login.jsp GET 으로 초기 JSESSIONID 확보 → (2)
MDCCOMS001D1.cmd POST(mbrId/pw 평문, 캡차·클라이언트 암호화 없음)로 그 세션을 로그인 승격.
승격된 JSESSIONID 는 login.jsp 가 심은 쿠키와 동일 값이므로 cookiejar 로 두 호출을 이어
그 값을 그대로 돌려준다. 쿠키는 메모리 cookiejar 에만 둔다(디스크·레이크 미기록) — mbr_id/
pw 는 env 로만 주입한다.

⚠️ 계정 파이프라인 전용: 같은 계정으로 사람이 동시 로그인하면 CD011(중복 로그인)로
실패한다(계정당 동시세션 1개). CD001 아닌 응답은 조용한 기본값 없이 fail-loud 한다.

PoliteClient(운반 코어)는 응답 헤더(Set-Cookie)를 노출하지 않아 여기선 재사용하지 않는다 —
로그인은 run 당 2콜뿐이고 쿠키 세션 관리가 필요해 stdlib cookiejar opener 를 직접 쓴다.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar

LOGIN_PAGE = "https://data.krx.co.kr/contents/MDC/COMS/client/view/login.jsp?site=mdc"
LOGIN_ENDPOINT = "https://data.krx.co.kr/contents/MDC/COMS/client/MDCCOMS001D1.cmd"
# KRX 는 브라우저 UA 가 아니면 요청을 거른다(라이브 실측) — getJsonData 도 같은 UA 를 쓴다.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class KrxAuth:
    """KRX 계정(mbr_id/pw) → 로그인 승격 JSESSIONID. 최초 session()에서 1회 로그인 후 캐시."""

    def __init__(self, mbr_id: str, pw: str, *, timeout: float = 10.0):
        self.mbr_id = mbr_id
        self.pw = pw
        self.timeout = timeout
        self._jsessionid: str | None = None

    def session(self) -> str:
        """캐시된 세션 쿠키가 있으면 재사용, 없으면 1회 로그인한다(run 당 1회 규약)."""
        if self._jsessionid is None:
            self._jsessionid = self._login()
        return self._jsessionid

    def _login(self) -> str:
        jar = CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        headers = {"User-Agent": USER_AGENT}

        # 1) 초기 JSESSIONID 확보 — 이 쿠키를 로그인 POST 가 이어받아 서버가 승격한다.
        opener.open(
            urllib.request.Request(LOGIN_PAGE, headers=headers), timeout=self.timeout
        ).read()

        # 2) 로그인 POST — mbrId/pw 평문(캡차·암호화 없음, 라이브 실측). 빈 필드도 그대로 보낸다.
        body = urllib.parse.urlencode(
            {"mbrNm": "", "telNo": "", "di": "", "certType": "",
             "mbrId": self.mbr_id, "pw": self.pw}
        ).encode("utf-8")
        raw = opener.open(
            urllib.request.Request(
                LOGIN_ENDPOINT, data=body,
                headers={**headers, "Content-Type": "application/x-www-form-urlencoded"},
            ),
            timeout=self.timeout,
        ).read()

        data = json.loads(raw.decode("utf-8", errors="replace"))
        code = data.get("_error_code")
        if code != "CD001":
            # CD011=중복 로그인(전용 계정 위반), 그 외=자격증명·폼 오류 — 조용히 넘기지 않고 fail-loud.
            detail = data.get("_error_msg") or data
            raise RuntimeError(f"KRX 로그인 실패: {code} ({detail})")

        jsessionid = next((c.value for c in jar if c.name == "JSESSIONID"), None)
        if not jsessionid:
            # CD001 인데 세션 쿠키가 없으면(스키마 드리프트) 조용한 성공으로 위장하지 않는다.
            raise RuntimeError("KRX 로그인 성공(CD001)했으나 JSESSIONID 쿠키가 없다")
        return jsessionid
