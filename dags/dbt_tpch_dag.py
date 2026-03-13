from datetime import datetime
from pathlib import Path

from cosmos import DbtDag, ProjectConfig, ProfileConfig, ExecutionConfig, RenderConfig
from cosmos.constants import ExecutionMode, LoadMode

DBT_PROJECT_PATH = Path("/stackable/app/git-0/current/dags/dbt/tpch_demo")

dbt_tpch_dag = DbtDag(
    dag_id="dbt_tpch_demo",
    schedule="@daily",
    start_date=datetime(2024, 1, 1),
    catchup=False,
    project_config=ProjectConfig(
        dbt_project_path=DBT_PROJECT_PATH,
    ),
    profile_config=ProfileConfig(
        profiles_yml_filepath=DBT_PROJECT_PATH / "profiles.yml",
        profile_name="tpch_demo",
        target_name="dev",
    ),
    render_config=RenderConfig(
        load_method=LoadMode.CUSTOM,
    ),
    execution_config=ExecutionConfig(
        execution_mode=ExecutionMode.VIRTUALENV,
        virtualenv_dir=Path("/tmp/dbt_venv"),
    ),
    operator_args={
        "py_system_site_packages": False,
        "py_requirements": ["dbt-trino"],
        "install_deps": True,
    },
    default_args={
        "retries": 1,
    },
)
