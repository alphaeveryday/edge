"""내보내기 람다의 저장 규칙을 고정한다. AWS 를 부르지 않는다.

    python3 -m unittest discover -s infra/terraform/modules/landing-export/tests -v
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from handler import export, strip_internal  # noqa: E402

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
        self.assertEqual(result, {'date': '2026-10-12', 'written': 1, 'missing': ['069500']})

    def test_writing_nothing_is_a_failure(self):
        # 조회 경로가 바뀌어도 404 라 '발행본 없음'과 구분되지 않는다. 0건을 성공으로 두면 조용히 낡는다.
        with self.assertRaises(RuntimeError):
            self.run_export({})


if __name__ == '__main__':
    unittest.main()
