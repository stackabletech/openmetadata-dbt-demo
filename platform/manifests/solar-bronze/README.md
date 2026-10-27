The solar bronze streaming job is no longer managed here: it is started (and restarted) by the
Airflow DAG solar_bronze_watchdog from platform/manifests/solar-pipeline/. This directory and
platform/applications/solar-bronze.yaml are removed in the next commit, after ArgoCD has pruned
the old SparkApplication.
