"""canonical 존 — **Iceberg 테이블 정의가 코드에 있다. SQL 문자열이 흩어지면 규약이 갈린다.**

조회 컨벤션은 Athena + Iceberg 다. 그런데 DDL 을 손으로 쓰면 테이블마다 파티션 변환·압축
속성·타입이 갈리고, 그 차이를 나중에 발견한다(파티션이 잘못 잡힌 테이블은 다시 써야 한다).
그래서 테이블을 **데이터로 선언**하고 DDL 을 생성한다 - 규약 위반이 생성 시점에 걸린다.

기존 자산과의 관계.

    market_data_{kr,us,common}   alphamale 프로토타입의 Glue DB. **버킷이 비어 있고**
                                 Iceberg 메타데이터가 없어 34개 테이블 전부 조회 실패한다
                                 (ICEBERG_MISSING_METADATA). 유령 카탈로그이므로 건드리지
                                 않는다 - 정리는 그쪽 소관이다
    market_data (워크그룹)        engine v3 · 결과 위치 강제. **재사용한다** - 워크그룹을
                                 새로 만들 이유가 없고, 계정 안에 둘이 생기면 비용 추적이 갈린다
    edge_lake_draft / edge_lake  edge 전용 Glue DB **신설**. 존 격리를 접두사가 아니라 DB 로
                                 한다 - Iceberg 는 접두사 격리가 안 통한다(테이블 location 이
                                 카탈로그에 박히므로 같은 테이블에 접두사만 다른 데이터를
                                 넣을 수 없다). raw 는 접두사(ndjson) · canonical 은 DB
"""

from .athena import Athena, AthenaError
from .financials import merge_statement_line
from .reports import merge_report_current
from .tables import REPORT_CURRENT, STATEMENT_LINE, Table

__all__ = ["Athena", "AthenaError", "REPORT_CURRENT", "STATEMENT_LINE", "Table",
           "merge_report_current", "merge_statement_line"]
