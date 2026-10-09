"""Example DAG of the marketing team (Airflow multi-tenancy demo).

Only members of the Keycloak group /marketing (and admins) see and run DAGs whose ID starts with
`marketing_`: the OPA policy in platform/manifests/opa/rego-airflow-policies.yaml assigns DAGs to
teams by that prefix. The tasks only log a few lines; no data is read or written.
"""

import time
from datetime import datetime

from airflow.sdk import dag, task


@dag(
    dag_id="marketing_campaign_report",
    description="Marketing: weekly campaign performance report (demo, no real data)",
    schedule=None,
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=["marketing", "team-demo"],
    default_args={"owner": "marketing"},
)
def marketing_campaign_report():
    @task
    def collect_campaign_metrics() -> dict:
        time.sleep(5)
        metrics = {"spring_sale": {"clicks": 12840, "conversions": 412}, "newsletter": {"clicks": 5310, "conversions": 98}}
        print(f"Collected metrics for {len(metrics)} campaigns")
        return metrics

    @task
    def compute_conversion_rates(metrics: dict) -> dict:
        rates = {name: round(m["conversions"] / m["clicks"] * 100, 2) for name, m in metrics.items()}
        for name, rate in rates.items():
            print(f"{name}: {rate} % conversion")
        return rates

    @task
    def publish_report(rates: dict) -> None:
        best = max(rates, key=rates.get)
        print(f"Report published. Best campaign: {best} ({rates[best]} %)")

    publish_report(compute_conversion_rates(collect_campaign_metrics()))


marketing_campaign_report()
