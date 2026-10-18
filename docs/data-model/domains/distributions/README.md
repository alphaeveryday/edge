# Cash distributions

![Physical table](erd.svg)

One source-event revision per market, ticker and raw collection run. Like other public source observations, ingestion does not require a pre-existing master. The ontology view resolves existing instrument IDs using the database-unique `(market_code, ticker)` pair; unmatched references remain null. No ID is fabricated. This is a view join, not a physical foreign key. Latest revision selection precedes publication filtering. Amounts are original-currency gross cash per share. Customer holdings and account receipts are not stored here.
