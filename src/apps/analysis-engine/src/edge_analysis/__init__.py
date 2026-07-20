"""EDGE analysis-engine: KODEX-semiconductor daily ETF explanation.

Consumer of the unified pipeline's feature outputs (ADR-0028, ALPHA-411/412):
it reads the pipeline-written ``price_movement_trigger`` (L0 gate) and the
assembled ``source_event`` lineage, decomposes the ETF move from the S3 lake,
and produces the daily explanation. Runs as a single ECS Fargate task, the
``analyze`` phase of the Step Functions state machine.

Layering:
    domain/     pure logic + models (no I/O)
    adapters/   I/O boundaries (S3 lake, Event Store, DeepSeek, run archive)
    pipeline    orchestration (dependency-injected)
    cli         composition root
"""
