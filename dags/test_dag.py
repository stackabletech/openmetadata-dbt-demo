from datetime import datetime

from airflow import DAG
from airflow.operators.bash import BashOperator

with DAG(
    dag_id="test_dag",
    schedule=None,
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["test"],
) as dag:
    BashOperator(
        task_id="hello",
        bash_command="echo 'DAG sync is working!'",
    )
