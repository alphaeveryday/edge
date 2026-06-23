#!/usr/bin/env python3
"""Daily analysis entrypoint -- the date is the ONLY argument (UTC).

News ingestion is NOT our concern: the after-close news for each day already lands
in the DB (``market.us_fmp_news_articles``). This job reads that day's news per
ticker, runs the trained EDGE model for EVERY universe ticker, and writes the
structured result + a plain-language LLM interpretation to ``market.us_analysis_table``
(keyed by trade_date, ticker).

    python analyze_daily.py --date 2026-06-19      # UTC date; default = today (UTC)

Env:
  NEWSDB_HOST/PORT/NAME/USER + (PGPW | RDS_SECRET_FILE)   RDS connection (via tunnel)
  ARTIFACTS_DIR (default model_artifacts_temporal)        trained model dir
  EDGE_EMBED_FILE                                         embedding cache filename
  LLM_API_KEY (or OPENAI_API_KEY)                         OpenAI key
  LLM_MODEL (default gpt-5.5-mini), LLM_BASE_URL          LLM config
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from edge_event_model import config
from edge_event_model.features import build_news_windows, load_ohlc, load_ff5
from edge_event_model.features.news_arm import TitleEmbedder, assign_trade_dates
from edge_event_model.models.combine import close_confidence
from edge_event_model.model_io import load_artifacts

TABLE = "market.us_analysis_table"
NEWS_TABLE = "market.us_fmp_news_articles"
DEFAULT_LLM_MODEL = "gpt-4o-mini"

DDL = f"""
CREATE SCHEMA IF NOT EXISTS market;
CREATE TABLE IF NOT EXISTS {TABLE} (
    trade_date            date NOT NULL,
    ticker                text NOT NULL,
    market                text NOT NULL DEFAULT 'US',
    company               text,
    sector                text,
    predicted_return      double precision,
    predicted_close_price double precision,
    predicted_high_price  double precision,
    predicted_direction   integer,
    normal_return         double precision,
    abnormal_return       double precision,
    close_confidence      double precision,
    high_confidence       double precision,
    news_count            integer,
    top_headlines         jsonb,
    analysis_json         jsonb,
    llm_interpretation    text,
    llm_model             text,
    model_version         text,
    created_at            timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (trade_date, ticker)
);
COMMENT ON TABLE {TABLE} IS '일자별 종목 분석 결과 + 일반 사용자용 LLM 해석';
"""

_COLS = ("ticker", "market", "company", "sector", "predicted_return", "predicted_close_price",
         "predicted_high_price", "predicted_direction", "normal_return", "abnormal_return",
         "close_confidence", "high_confidence", "news_count", "model_version")

UPSERT = f"""
INSERT INTO {TABLE} (trade_date,{','.join(_COLS)},top_headlines,analysis_json,llm_interpretation,llm_model)
VALUES (%(trade_date)s,{','.join('%('+c+')s' for c in _COLS)},%(top_headlines)s,%(analysis_json)s,
        %(llm_interpretation)s,%(llm_model)s)
ON CONFLICT (trade_date,ticker) DO UPDATE SET
    market=EXCLUDED.market, company=EXCLUDED.company, sector=EXCLUDED.sector,
    predicted_return=EXCLUDED.predicted_return, predicted_close_price=EXCLUDED.predicted_close_price,
    predicted_high_price=EXCLUDED.predicted_high_price, predicted_direction=EXCLUDED.predicted_direction,
    normal_return=EXCLUDED.normal_return, abnormal_return=EXCLUDED.abnormal_return,
    close_confidence=EXCLUDED.close_confidence, high_confidence=EXCLUDED.high_confidence,
    news_count=EXCLUDED.news_count, top_headlines=EXCLUDED.top_headlines,
    analysis_json=EXCLUDED.analysis_json, llm_interpretation=EXCLUDED.llm_interpretation,
    llm_model=EXCLUDED.llm_model, model_version=EXCLUDED.model_version, created_at=now()
"""


def _dsn() -> dict:
    pw = os.environ.get("PGPW")
    if not pw and os.environ.get("RDS_SECRET_FILE"):
        pw = json.loads(Path(os.environ["RDS_SECRET_FILE"]).read_text(encoding="utf-8"))["password"]
    return dict(
        host=os.environ.get("NEWSDB_HOST", "127.0.0.1"), port=int(os.environ.get("NEWSDB_PORT", "15433")),
        dbname=os.environ.get("NEWSDB_NAME", "newspipeline"), user=os.environ.get("NEWSDB_USER", "pipeline_admin"),
        password=pw, sslmode="require", connect_timeout=30,
    )


def _conf_bucket(cc: float) -> str:
    return "높음" if cc >= 0.55 else ("보통" if cc >= 0.40 else "낮음")


def _company(ticker: str) -> str:
    for a in config.UNIVERSE:
        if a.ticker == ticker:
            return a.company
    return ticker


def _fetch_news(conn, tickers, start: date, end: date) -> pd.DataFrame:
    sql = f"""
        SELECT ticker, article_id AS news_id, title, published_at
        FROM {NEWS_TABLE}
        WHERE ticker = ANY(%s)
          AND (published_at AT TIME ZONE 'UTC')::date BETWEEN %s AND %s
    """
    df = pd.read_sql_query(sql, conn, params=(list(tickers), start, end))
    if not df.empty:
        df["published_at"] = pd.to_datetime(df["published_at"], utc=True).dt.tz_localize(None)
    return df


def _build_messages(a: dict) -> list[dict]:
    sys_p = (
        "너는 일반 개인투자자에게 '오늘 이 종목의 종가와 등락'을 쉽게 설명해 주는 도우미야. "
        "우리 모델은 캘리브레이션 검증을 통과해 모델 종가가 실제 종가와 거의 일치하므로, "
        "아래 수치를 '오늘의 종가와 변동'으로 보고 그날 주가가 그렇게 움직인 배경(뉴스)을 설명해. "
        "'예상된다·전망·예측' 같은 미래 추측형 표현은 쓰지 말고, 그날의 종가와 등락폭을 설명하는 어조로 써. "
        "상관계수·표준편차·z-score 같은 통계 용어는 쓰지 말고, 2~4문장 한국어로, 단정적 매수/매도 권유는 하지 마."
    )
    move = "올라" if (a["predicted_direction"] or 0) > 0 else "내려"
    heads = "; ".join(a["top_headlines"][:3]) or "당일 관련 뉴스 없음"
    user_p = (
        f"종목: {a['company']}({a['ticker']}), 날짜: {a['trade_date']}\n"
        f"당일 종가: {a['predicted_close_price']:.2f} (전일 종가 {a['prev_close']:.2f} 대비 {a['predicted_return']*100:+.1f}% {move})\n"
        f"반영된 당일 뉴스: {a['news_count']}건\n주요 헤드라인: {heads}\n"
        f"이벤트성 급변동: {'있음' if a['is_event'] else '없음'}\n설명 신뢰도: {_conf_bucket(a['close_confidence'])}\n\n"
        "위 종가와 등락을 바탕으로, 오늘 이 종목이 어떻게 움직였고 왜 그랬는지 일반 사용자에게 설명해줘."
    )
    return [{"role": "system", "content": sys_p}, {"role": "user", "content": user_p}]


def _template_summary(a: dict) -> str:
    move = "올라" if (a["predicted_direction"] or 0) > 0 else "내려"
    s = (f"{a['company']}({a['ticker']})는 {a['trade_date']} 전일 종가 {a['prev_close']:.0f} 대비 "
         f"{a['predicted_return']*100:+.1f}% {move} 약 {a['predicted_close_price']:.0f}에 마감했습니다. "
         f"당일 반영된 뉴스는 {a['news_count']}건이며, 설명 신뢰도는 {_conf_bucket(a['close_confidence'])} 수준입니다.")
    if a["top_headlines"]:
        s += f" 주요 뉴스: \"{a['top_headlines'][0][:80]}\"."
    return s + " 이는 모델 분석 결과이며 투자 권유가 아닙니다."


def generate_interpretation(a: dict) -> tuple[str, str]:
    key = os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    model = os.environ.get("LLM_MODEL", DEFAULT_LLM_MODEL)
    if not key:
        return _template_summary(a), "template-fallback"
    base = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    body = json.dumps({"model": model, "messages": _build_messages(a),
                       "max_completion_tokens": 600}).encode()  # gpt-5 family: no custom temperature
    req = urllib.request.Request(f"{base}/chat/completions", data=body,
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.loads(r.read().decode())
        txt = (d["choices"][0]["message"]["content"] or "").strip()
        return (txt, model) if txt else (_template_summary(a), f"{model}(empty->template)")
    except Exception as exc:  # one ticker's LLM failure must not abort the batch
        print(f"  [warn] LLM 실패({a['ticker']}): {exc}")
        return _template_summary(a), f"{model}(error->template)"


def run_for_date(target_utc: date, artifacts_dir: str) -> dict:
    import psycopg2
    from psycopg2.extras import Json

    news_model, spread_model, factor_latest, meta = load_artifacts(artifacts_dir)
    latest = {row.ticker: row for row in factor_latest.itertuples(index=False)}
    s_close = float(meta.get("s_close", 1.0)) or 1.0
    model_version = str(meta.get("model_version", "ff5_temporal_news_v3"))
    tickers = [t for t in config.TICKERS if t in latest]
    ts = pd.Timestamp(target_utc)

    ohlc = load_ohlc(tickers, end=target_utc)
    trading_dates = {t: np.sort(g["trade_date"].unique()) for t, g in ohlc.groupby("ticker", sort=False)}
    try:
        _ff5 = load_ff5(end=target_utc)
        frow = _ff5.iloc[-1].to_dict() if len(_ff5) else None
    except Exception as exc:
        print(f"  [warn] FF5 미사용(alpha 드리프트 baseline): {exc}"); frow = None

    conn = psycopg2.connect(**_dsn())
    saved = 0
    try:
        news = _fetch_news(conn, tickers, target_utc - timedelta(days=60), target_utc)
        embedder = TitleEmbedder()
        _, windows = build_news_windows(news, trading_dates, embedder=embedder)
        dated = assign_trade_dates(news, trading_dates) if not news.empty else news

        cur = conn.cursor()
        cur.execute(DDL)
        for ticker in tickers:
            fl = latest[ticker]
            prior = ohlc[(ohlc["ticker"] == ticker) & (ohlc["trade_date"] < ts)].sort_values("trade_date")
            prev_close = float(prior["close"].iloc[-1]) if len(prior) else float(fl.last_close)
            ncount, top_heads = 0, []
            if dated is not None and not dated.empty:
                same = dated[(dated["ticker"] == ticker) & (dated["trade_date"] == ts)].sort_values("published_at")
                ncount = int(len(same))
                top_heads = [str(t)[:140] for t in same["title"].tail(3).tolist()][::-1]

            fdf = pd.DataFrame([{"ticker": ticker, "trade_date": ts, "normal_return": float(fl.alpha),
                                 "spread_lag_mean": float(fl.spread_lag_mean),
                                 "spread_lag_std": float(fl.spread_lag_std), "news_count": ncount}])
            abn, sigma = news_model.predict_abnormal(fdf, windows)
            abn, sigma = float(abn[0]), float(sigma[0])
            spread_out = spread_model.predict(fdf)
            spread = float(spread_out["spread_pred"].iloc[0]); high_conf = float(spread_out["high_confidence"].iloc[0])
            cc = float(close_confidence(np.array([sigma]), s_close)[0])
            alpha = float(fl.alpha)
            if frow is not None:
                normal = float(frow["rf"]) + alpha + (
                    float(fl.beta_mkt_rf) * float(frow["mkt_rf"])
                    + float(fl.beta_smb) * float(frow["smb"])
                    + float(fl.beta_hml) * float(frow["hml"])
                    + float(fl.beta_rmw) * float(frow["rmw"])
                    + float(fl.beta_cma) * float(frow["cma"]))
            else:
                normal = alpha
            close_ret = normal + abn
            predicted_close = prev_close * float(np.exp(close_ret))
            predicted_high = predicted_close * float(np.exp(max(spread, 0.0)))

            analysis = {
                "ticker": ticker, "market": "US", "company": _company(ticker),
                "sector": config.SECTOR_BY_TICKER.get(ticker), "trade_date": str(target_utc),
                "predicted_return": close_ret, "predicted_close_price": predicted_close,
                "predicted_high_price": predicted_high, "predicted_direction": int(np.sign(close_ret)),
                "normal_return": normal, "abnormal_return": abn, "close_confidence": cc, "high_confidence": high_conf,
                "news_count": ncount, "top_headlines": top_heads, "prev_close": prev_close,
                "is_event": bool(abs(abn) >= config.ABS_ABNORMAL_THRESHOLD), "model_version": model_version,
            }
            interp, llm_model = generate_interpretation(analysis)
            analysis["llm_interpretation"] = interp
            row = {k: analysis.get(k) for k in _COLS}
            row.update({"trade_date": target_utc, "top_headlines": Json(top_heads),
                        "analysis_json": Json(analysis), "llm_interpretation": interp, "llm_model": llm_model})
            cur.execute(UPSERT, row)
            saved += 1
            d = "▲" if analysis["predicted_direction"] > 0 else "▼"
            print(f"  {ticker:6s} {d} ret={close_ret*100:+.2f}% close={predicted_close:.2f} "
                  f"conf={cc:.2f} news={ncount} llm={llm_model}")
        conn.commit()
    finally:
        conn.close()
    response = {"status": "completed", "market": "US", "trade_date": str(target_utc),
                "saved": saved, "tickers": tickers, "table": TABLE, "model_version": model_version}
    print(f"[analyze_daily] {response}")
    return response


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Daily all-ticker analysis -> us_analysis_table (date arg only, UTC)")
    ap.add_argument("--date", default=datetime.now(timezone.utc).date().isoformat(),
                    help="UTC date YYYY-MM-DD (default: today UTC)")
    args = ap.parse_args(argv)
    artifacts = os.environ.get("ARTIFACTS_DIR", "model_artifacts_temporal")
    resp = run_for_date(date.fromisoformat(args.date), artifacts)
    print(json.dumps(resp, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
