# DART disclosure event assembly design

## Goal

Make parsed DART supply-contract disclosures consumable by the analysis engine and
eligible to join the same event thread as news about the same contract. Preserve the
existing business-segment path: canonical Parquet in S3 plus
`business_segment_fact` in RDB. Business-segment rows are facts, not event triggers.

Jira: ALPHA-895.

## Current gap

`load-disclosure` writes `document(DISCLOSURE) -> disclosure_document ->
disclosure_fact -> supply_contract_fact`, but analysis consumes `source_event` with
arguments, measures, and evidence. The existing `assemble-events` runtime only reads
NEWS documents. It also performs LLM classification, which is unnecessary for a
typed disclosure fact.

The schema already has `supply_contract_fact.counterparty_actor_id` and
`contract_object_concept_id`. The loader currently writes neither: the actor is
always `NULL`, and the parsed contract object is dropped. Therefore a disclosure
cannot produce the same contract identity as a news event.

## Chosen design

Add a deterministic disclosure assembler separate from the NEWS assembler. It reads
typed supply facts and maps them to the existing ontology without an LLM:

| Canonical event field | DART source |
| --- | --- |
| event type | `COMPANY.CONTRACT.SIGNING` |
| predicate | `SIGN` |
| lifecycle stage | `DEFINITIVE_SIGNED` |
| `SUPPLIER` | common-stock instrument mapped from `disclosure_document.issuer_actor_id` |
| `CUSTOMER` | common-stock instrument mapped from resolved `counterparty_actor_id`; unresolved/withheld stays absent |
| `CONTRACT_OBJECT` | deterministic concept minted from parsed contract object |
| `CONTRACT_VALUE` | `contract_amount_krw`, KRW, `TOTAL` |
| `CONTRACT_DURATION` | start/end dates |
| `EFFECTIVE_DATE` | contract start date when present |
| evidence | source DART document and disclosure fact |

The loader resolves the raw counterparty with the existing entity-resolution index
and mints the contract-object concept with the same concept-key/ID utilities used by
NEWS assembly. It always preserves the raw counterparty name. Resolution failure is
not converted into a fabricated actor.

The assembler persists through shared event persistence primitives and calls the
existing `thread_events` function. Thread identity remains source-neutral:
`SUPPLIER + CUSTOMER + CONTRACT_OBJECT`. Consequently DART and NEWS join only when
all three normalized identity values match. A withheld or unresolved customer goes
to the existing UNKNOWN policy; it must not be falsely joined by raw-name guessing.

## Forward pipeline

For each disclosure-worker window:

1. collect DART raw documents;
2. normalize supply-contract and annual business-segment Parquet;
3. load both typed facts into the existing RDB tables;
4. assemble newly loaded supply-contract facts into canonical disclosure events;
5. link those events with the shared thread algorithm.

An assembly failure fails the window. It is not silently treated as a successfully
loaded disclosure, because that would reproduce the current analysis gap.

## Backfill

Expose a disclosure backfill CLI whose default is all retained DART raw data and
whose optional `--from`/`--to` bounds restrict filing dates. It invokes the same
collect/normalize/load/assemble functions used by forward processing. Re-running a
range is idempotent through deterministic document, fact, assertion, event, and
thread identities.

The backfill regenerates both canonical Parquet datasets and typed RDB facts, but
only supply contracts are eventized. Counts for input, normalized facts, typed
facts, assembled/already-assembled events, unresolved identity roles, and failures
are emitted. Any skipped stage is explicit and produces a non-success result when
required output is missing.

## Data and migration impact

No new business-segment store or event ontology is introduced. No schema migration
is expected: the required supply columns and canonical event tables already exist.
The implementation changes the supply loader to populate existing nullable columns.

## Verification

- A loader test proves parsed counterparty/object values land in the existing supply
  fact columns while unresolved values remain explicit.
- An assembler test proves one typed supply fact creates the ontology assertion,
  event arguments/measures/evidence, and is idempotent.
- A thread test proves a disclosure event and NEWS event with identical normalized
  identity roles receive the same thread key; a missing customer uses UNKNOWN.
- Worker tests prove assembly runs after load and its failure fails the window.
- CLI tests prove default full retention and inclusive `--from`/`--to` bounds use the
  shared forward functions.
- Existing segment normalization/loading tests continue to pass, demonstrating that
  business-segment behavior did not change.

## Out of scope

- Eventizing business-segment revenue.
- Fuzzy raw-name thread joins.
- A new generic ontology or a second segment fact table.
- Supersession semantics for corrected DART filings.
