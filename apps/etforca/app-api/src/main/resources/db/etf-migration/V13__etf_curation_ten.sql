-- 출시 선별 ETF 10종과 그 테마. 동기화는 이 목록 밖 ETF 를 지운다.
DELETE FROM etf_curation;
DELETE FROM theme;
INSERT INTO theme (key, label, "group", hot, position) VALUES
    ('semicon',   '반도체',          'industry', true,  1),
    ('bank',      '은행',            'industry', false, 2),
    ('game',      '게임',            'industry', false, 3),
    ('battery',   '2차전지',         'industry', false, 4),
    ('defense',   '방산',            'industry', false, 5),
    ('bio',       '바이오',          'industry', false, 6),
    ('renewable', '신재생에너지',    'industry', false, 7),
    ('robot',     '휴머노이드 로봇', 'industry', false, 8),
    ('market',    '국내 대표지수',   'industry', false, 9),
    ('it',        'IT',              'industry', false, 10);

INSERT INTO etf_curation (etf_code, theme_key, sub, hot, manager, expense_ratio, listed_on, blurb) VALUES
    ('091160', 'semicon', 'KRX 반도체', true, '삼성자산운용', 0.45, '2006-06-27',
     'KRX 반도체 지수를 따라가요. 국내 반도체 기업에 나눠 담아요.'),
    ('091170', 'bank', 'KRX 은행', false, '삼성자산운용', 0.30, '2006-06-27',
     'KRX 은행 지수를 따라가요. 국내 대표 은행주에 나눠 담아요.'),
    ('300950', 'game', 'FnGuide 게임 산업', false, '삼성자산운용', 0.45, '2018-07-24',
     'FnGuide 게임 산업 지수를 따라가요. 국내 게임사에 나눠 담아요.'),
    ('305720', 'battery', 'FnGuide 2차전지 산업', false, '삼성자산운용', 0.45, '2018-09-12',
     'FnGuide 2차전지 산업 지수를 따라가요. 2차전지 밸류체인 기업에 나눠 담아요.'),
    ('449450', 'defense', 'FnGuide K-방위산업', false, '한화자산운용', 0.45, '2023-01-05',
     'FnGuide K-방위산업 지수를 따라가요. 국내 방산 기업에 나눠 담아요.'),
    ('261070', 'bio', '코스닥 150 생명기술', false, '미래에셋자산운용', 0.40, '2016-12-15',
     '코스닥 150 생명기술 지수를 따라가요. 코스닥 바이오 기업에 나눠 담아요.'),
    ('377990', 'renewable', 'FnGuide 신재생에너지', false, '미래에셋자산운용', 0.50, '2021-03-05',
     'FnGuide 신재생에너지 지수를 따라가요. 신재생에너지 관련 기업에 나눠 담아요.'),
    ('0177X0', 'robot', 'Akros K-휴머노이드 로봇 TOP2+', false, '한국투자신탁운용', 0.45, '2026-04-07',
     '휴머노이드 로봇 기업 15종목에 담고 대표 2종목은 20%씩 담아요.'),
    ('069500', 'market', '코스피 200', false, '삼성자산운용', 0.15, '2002-10-14',
     '코스피 200 지수를 따라가요. 한국 대표 200개 종목에 나눠 담아요.'),
    ('266370', 'it', 'KRX 정보기술', false, '삼성자산운용', 0.45, '2017-03-28',
     'KRX 정보기술 지수를 따라가요. 국내 IT 기업에 나눠 담아요.');
