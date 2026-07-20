"""EDGE analysis-engine: KODEX-semiconductor daily news normalization + explanation.

Reads the trade day's news titles from the S3 canonical lake, normalizes the
un-normalized ones into the Cloud Event Store (document -> assertion ->
source_event), threads KODEX-constituent events, and produces the daily ETF
explanation. Runs as a single ECS Fargate task invoked by a Step Functions
state machine.
"""

__all__ = ["daily_pipeline", "ontology"]
