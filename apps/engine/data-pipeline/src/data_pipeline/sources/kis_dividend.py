"""KIS domestic cash-distribution observations; no customer account data.

An incomplete response is split by record-date range. A one-day response that is
still incomplete fails explicitly. Raw observations retain vendor bytes and are
never converted into zero dividends on failure. US endpoints remain probes until
issuer reconciliation resolves the observed identifier and amount conflicts.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode

from .kis_auth import KisAuth, domain_for
from ..steps.source_observations import RawObject

PATH = "/uapi/domestic-stock/v1/ksdinfo/dividend"
TR_ID = "HHKDB669102C0"


class KisDividendSource:
    """Collect public domestic dividend responses with shared KIS authentication."""
    def __init__(self, app_key, app_secret, client, env="prod"):
        self.client = client
        self.auth = KisAuth(app_key, app_secret, client, env)
        self.base = domain_for(env)
        self.headers = {"appkey": app_key, "appsecret": app_secret,
                        "tr_id": TR_ID, "custtype": "P"}

    def fetch(self, instrument: dict, start: date, end: date) -> list[RawObject]:
        """Preserve response bytes and split incomplete record-date windows."""
        symbol = instrument["ticker"]
        if not re.fullmatch(r"[0-9A-Z]{6}", symbol) or start > end:
            raise ValueError("Invalid domestic dividend request")
        objects = []

        def window(first, last):
            """Append one response and recursively collect incomplete subwindows."""
            request = {"ticker": symbol,
                       "market_code": instrument["market_code"], "tr_id": TR_ID,
                       "from": first.isoformat(), "to": last.isoformat()}
            params = dict(CTS="", GB1="0", F_DT=first.strftime("%Y%m%d"),
                          T_DT=last.strftime("%Y%m%d"), SHT_CD=symbol, HIGH_GB="")
            body, status, detail = None, "error", None
            try:
                headers = {**self.headers, "authorization": f"Bearer {self.auth.token()}"}
                response_headers = {}
                body = self.client.request("GET", self.base + PATH + "?" + urlencode(params),
                    headers=headers, decode=False, response_headers=response_headers)
                data = json.loads(body)
                if data.get("rt_cd") != "0":
                    detail = "kis_api_error"
                elif not isinstance(data.get("output1"), list):
                    detail = "invalid_output"
                else:
                    continuation = response_headers.get("tr_cont", "").strip()
                    request["continuation"] = continuation
                    if continuation in ("M", "F"):
                        if first == last:
                            detail = "incomplete_single_day"
                        else:
                            request["superseded_by_split"] = True
                            status = "ok"
                    elif continuation in ("D", "E"):
                        status = "ok" if data["output1"] else "empty"
                    else:
                        detail = "unknown_continuation"
            except Exception as error:
                # Transport exceptions can include credential-bearing headers/URLs.
                detail = type(error).__name__
            objects.append(RawObject("kis", "market", "KR",
                f"{symbol}-{first.isoformat()}-{last.isoformat()}", "json", body,
                request, status, detail, datetime.now(timezone.utc).isoformat()))
            if request.get("superseded_by_split"):
                middle = first + (last - first) // 2
                window(first, middle)
                window(middle + timedelta(days=1), last)

        window(start, end)
        return objects
