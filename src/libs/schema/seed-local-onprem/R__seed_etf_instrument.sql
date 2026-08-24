-- ============================================================================
-- 로컬/데모 전용 시드 — SSOT(migrations-onprem) 밖이다.
-- 종목 마스터(etf_instrument)는 증권사 환경 소유 데이터라 마이그레이션이 채우지
-- 않는다 — 이 시드는 그 데이터가 "이미 있다"를 로컬/데모에서 흉내 낸다.
-- 원천: cloud dev DB instrument(instrument_type='ETF') JOIN entity.display_name,
-- 2026-08-24 db-query 실측 38종(전부 XKRX). repeatable(R__) + 멱등.
-- ============================================================================

INSERT INTO etf_instrument (etf_ticker, etf_name) VALUES
    ('0005G0', 'IBK K-AI반도체코어테크'),
    ('0093A0', 'RISE AI반도체TOP10'),
    ('0167A0', 'SOL AI반도체TOP2플러스'),
    ('0176P0', 'FOCUS AI반도체위클리고정커버드콜'),
    ('0177X0', 'ACE K휴머노이드로봇산업TOP2+'),
    ('0182R0', '1Q K반도체TOP2+'),
    ('0190G0', 'KODEX 반도체타겟위클리커버드콜'),
    ('0210A0', 'ACE K반도체TOP2+'),
    ('069500', 'KODEX 200'),
    ('091160', 'KODEX 반도체'),
    ('091170', 'KODEX 은행'),
    ('091230', 'TIGER 반도체'),
    ('139260', 'TIGER 200 IT'),
    ('261060', 'TIGER 코스닥150IT'),
    ('261070', 'TIGER 코스닥150바이오테크'),
    ('266370', 'KODEX IT'),
    ('300950', 'KODEX 게임산업'),
    ('305720', 'KODEX 2차전지산업'),
    ('363580', 'KODEX 200IT TR'),
    ('377990', 'TIGER Fn신재생에너지'),
    ('388420', 'RISE 비메모리반도체액티브'),
    ('395160', 'KODEX AI반도체TOP2플러스'),
    ('395270', 'HANARO Fn K-반도체'),
    ('396500', 'TIGER 반도체TOP10'),
    ('449450', 'PLUS K방산'),
    ('455850', 'SOL AI반도체소부장'),
    ('469150', 'ACE AI반도체TOP3+'),
    ('471760', 'TIGER AI반도체핵심공정'),
    ('471780', 'TIGER 코리아테크액티브'),
    ('471990', 'KODEX AI반도체핵심장비'),
    ('474590', 'WON 반도체밸류체인액티브'),
    ('475300', 'SOL 반도체전공정'),
    ('475310', 'SOL 반도체후공정'),
    ('476260', 'HANARO 반도체핵심공정주도주'),
    ('482030', 'KoAct 반도체&2차전지핵심소재액티브'),
    ('486240', 'DAISHIN343 AI반도체&인프라액티브'),
    ('488210', 'KIWOOM K-반도체북미공급망'),
    ('494220', 'UNICORN SK하이닉스밸류체인액티브')
ON CONFLICT (etf_ticker) DO UPDATE SET etf_name = EXCLUDED.etf_name, updated_at = now();
