"""내보내기 람다의 저장 규칙을 고정한다. AWS 를 부르지 않는다.

    python3 -m unittest discover -s infra/terraform/modules/landing-export/tests -v
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from handler import export, render_page, sitemap, strip_internal  # noqa: E402

SCREEN = {'outlook': {'direction': '상승'},
          'detail': {'items': [{'id': 'a', 'tool_run_ids': ['r1'], 'source_links': [{'title': 't', 'url': 'u'}]}]},
          'conclusion': {'supports': [{'label': 'HBM', 'tool_run_ids': ['r2']}]}}


class ExportTest(unittest.TestCase):
    def run_export(self, published):
        saved = {}
        result = export(['091160', '069500'], '2026-10-12', lambda code, day: published.get((code, day)), saved.__setitem__)
        return result, saved

    def test_internal_run_ids_never_reach_the_public_file(self):
        # 랜딩 파일은 로그인 없이 열린다. 깊이에 상관없이 빠져야 하고, 화면이 쓰는 값은 남아야 한다.
        _, saved = self.run_export({('091160', '2026-10-12'): SCREEN})
        self.assertNotIn('tool_run_ids', saved['091160'])
        self.assertEqual(json.loads(saved['091160']), strip_internal(SCREEN))
        self.assertEqual(json.loads(saved['091160'])['detail']['items'][0]['source_links'][0]['url'], 'u')
        self.assertIn('상승', saved['091160'])  # 한글을 \\u 로 풀어 쓰지 않는다

    def test_etf_without_todays_publication_keeps_its_previous_file(self):
        # 그날 발행본이 없는 종목은 덮어쓰지 않는다 — 빈 파일로 덮으면 랜딩의 그 종목이 비어 버린다.
        result, saved = self.run_export({('091160', '2026-10-12'): SCREEN, ('069500', '2026-10-11'): SCREEN})
        self.assertEqual(list(saved), ['091160'])
        self.assertEqual(result, {'date': '2026-10-12', 'written': 1, 'missing': ['069500'], 'failed': {}})

    def test_one_failing_etf_does_not_block_the_rest_but_fails_the_run(self):
        # 앞 종목의 조회 오류가 뒤 종목의 갱신을 막으면 한 종목 장애가 랜딩 전체를 낡게 한다.
        # 그래도 성공으로 끝내면 안 된다 — 실패로 끝나야 알람이 울린다.
        saved = {}

        def fetch(code, day):
            if code == '091160':
                raise TimeoutError('timed out')
            return SCREEN

        with self.assertRaises(RuntimeError):
            export(['091160', '069500'], '2026-10-12', fetch, saved.__setitem__)
        self.assertEqual(list(saved), ['069500'])

    def test_running_out_of_time_reports_the_unvisited_etfs(self):
        # 람다가 제한 시간에 강제 종료되면 어느 종목이 빠졌는지 로그에도 남지 않는다.
        # 시간이 모자라면 남은 종목을 실패로 적고 실패로 끝내야 한다.
        saved, clock = {}, iter([False, True])
        with self.assertRaises(RuntimeError) as raised:
            export(['091160', '069500'], '2026-10-12', lambda code, day: SCREEN, saved.__setitem__,
                   out_of_time=lambda: next(clock))
        self.assertEqual(list(saved), ['091160'])
        self.assertIn('오류 1종', str(raised.exception))

    def test_writing_nothing_is_a_failure(self):
        # 조회 경로가 바뀌어도 404 라 '발행본 없음'과 구분되지 않는다. 0건을 성공으로 두면 조용히 낡는다.
        with self.assertRaises(RuntimeError):
            self.run_export({})


PUBLIC_SCREEN = {
    'outlook': {'direction': '강력하락'},
    'summary_card': {'title': 'KODEX 반도체, 한 달 뒤 더 낮아요', 'summary': '요약에만 있는 판단'},
    'conclusion': {'title': '한 달 뒤 더 낮아요', 'sentence': '결론에만 있는 판단', 'supports': [{'label': '지지 꼬리표'}]},
    'detail': {'items': [{'title_keyword': '항목 제목의 판단', 'sentences': [{'sentence': '근거 문장의 판단'}],
                          'source_links': [{'url': 'https://news.example/a', 'title': '기사 <제목>'},
                                           {'url': 'javascript:alert(1)', 'title': '나쁜 주소'}]}]},
    'factors': [{'type': '이슈', 'sticker': '스티커방향', 'sentence': '23일 ETF가 -0.7% 내렸어요 <b>'},
                {'type': '수급', 'sticker': '중립', 'sentence': '외국인이 5일 순매도했어요.'}],
    'publication': {'analysis_at': '2026-10-23T21:00:00+00:00'},
}


class PageTest(unittest.TestCase):
    def test_public_page_carries_no_outlook_judgement(self):
        # 종목별 방향은 검색 결과에 공개하지 않기로 했다(ALPHA-1454). 방향·결론·요약·근거 문장·스티커가
        # 하나라도 새면 그 결정이 깨진다 — 실을 수 있는 것은 오늘 일어난 일(factors)과 기사뿐이다.
        page = render_page('091160', 'KODEX 반도체', PUBLIC_SCREEN)
        for judgement in ('강력하락', '한 달 뒤 더 낮아요', '요약에만', '결론에만', '지지 꼬리표', '항목 제목의', '근거 문장의', '스티커방향'):
            self.assertNotIn(judgement, page)
        self.assertIn('외국인이 5일 순매도했어요.', page)
        self.assertIn('10월 24일 06:00', page)  # 분석 시각은 KST 로 적는다

    def test_llm_text_cannot_inject_markup_or_script_links(self):
        # 문장은 LLM 이 쓴 글이고 기사 주소는 바깥에서 온다. 그대로 넣으면 공개 페이지에 태그·스크립트 링크가 박힌다.
        page = render_page('091160', 'SOL 전고체배터리&실리콘음극재', PUBLIC_SCREEN)
        self.assertIn('-0.7% 내렸어요 &lt;b&gt;', page)
        self.assertIn('기사 &lt;제목&gt;', page)
        self.assertNotIn('javascript:', page)
        self.assertIn('SOL 전고체배터리&amp;실리콘음극재', page)

    def test_play_link_counts_installs_per_etf_page(self):
        # 설치를 종목 페이지별로 세려면 Play 링크 referrer 에 페이지와 종목이 남아야 한다.
        page = render_page('091160', 'KODEX 반도체', PUBLIC_SCREEN)
        self.assertIn('referrer=utm_source%3Detforca.com%26utm_medium%3Detf_page%26utm_campaign%3D091160', page)

    def test_sitemap_lists_every_etf_page(self):
        xml = sitemap(['091160', '0000J0'])
        self.assertIn('<loc>https://etforca.com/etf/091160</loc>', xml)
        self.assertIn('<loc>https://etforca.com/etf/0000J0</loc>', xml)


if __name__ == '__main__':
    unittest.main()
