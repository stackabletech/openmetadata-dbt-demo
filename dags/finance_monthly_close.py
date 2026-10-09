"""Example DAG of the finance team (Airflow multi-tenancy demo).

Only members of the Keycloak group /finance (and admins) see and run it: the OPA policy in
platform/manifests/opa/rego-airflow-policies.yaml assigns DAGs to teams by the tag `team:<name>`. The tasks only log a few lines; no data is read or written.
"""

import time
from datetime import datetime

from airflow.sdk import dag, task


@dag(
    dag_id="finance_monthly_close",
    description="Finance: month-end close checks (demo, no real data)",
    schedule=None,
    start_date=datetime(2026, 1, 1),
    catchup=False,
    # `team:finance`: the OPA policy gives this DAG to the Keycloak group /finance
    tags=["team:finance", "team-demo"],
    default_args={"owner": "finance"},
)
def finance_monthly_close():
    @task
    def load_ledger_totals() -> dict:
        time.sleep(5)
        totals = {"revenue": 1_284_500.00, "expenses": 963_220.50}
        print(f"Loaded ledger totals: {totals}")
        return totals

    @task
    def reconcile(totals: dict) -> float:
        margin = totals["revenue"] - totals["expenses"]
        print(f"Operating margin: {margin:,.2f}")
        return margin

    @task
    def sign_off(margin: float) -> None:
        print(f"Month-end close signed off (margin {margin:,.2f})")

    sign_off(reconcile(load_ledger_totals()))


finance_monthly_close()
