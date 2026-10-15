-- 앱 선별 ETF 와 원천 없는 표시 값. 동기화는 이 목록의 코드만 옮긴다.
CREATE TABLE etf_curation (
    etf_code       VARCHAR(6)    PRIMARY KEY,
    theme_key      VARCHAR(30)   NOT NULL,
    sub            VARCHAR(100),
    hot            BOOLEAN       NOT NULL DEFAULT false,
    manager        VARCHAR(50)   NOT NULL,
    expense_ratio  NUMERIC(5,2)  NOT NULL,   -- 연 %
    listed_on      DATE          NOT NULL,
    leverage       NUMERIC(3,1),             -- 배수, 해당 없으면 NULL
    hedged         BOOLEAN,                  -- 환헤지 여부, 해외 자산이 아니면 NULL
    blurb          VARCHAR(200)  NOT NULL
);

-- 테마는 선별 ETF 가 쓰는 것만.
DELETE FROM theme;
INSERT INTO theme (key, label, "group", hot, position) VALUES
    ('semicon', '반도체', 'industry', true, 1);

INSERT INTO etf_curation (etf_code, theme_key, sub, hot, manager, expense_ratio, listed_on, blurb) VALUES
    ('091160', 'semicon', 'KRX 반도체', true, '삼성자산운용', 0.45, '2006-06-27',
     'KRX 반도체 지수를 따라가요. 국내 반도체 기업에 나눠 담아요.');
