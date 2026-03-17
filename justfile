demo name:
    just infra {{name}}
    just kubeconfig {{name}}
    just deploy

deploy:
    stackablectl stack install forgejo --stack-file infrastructure/stack.yaml

seal-secrets:
    #!/usr/bin/env bash
    # Replicate directory structure from 'secrets' to 'platform'
    find secrets -type d | while read -r dir; do
        target_dir="${dir/secrets/platform}"
        echo "Creating $target_dir"
        mkdir -p "$target_dir"
    done
    # Process all yaml files
    find secrets -type f \( -name "*.yaml" -o -name "*.yml" \) | while read -r input_file; do
        output_path="${input_file/secrets/platform}"
        output_dir="$(dirname "$output_path")"
        output_filename="sealed-$(basename "$output_path")"
        output_file="$output_dir/$output_filename"
        echo "Processing: $input_file -> $output_file"
        kubeseal -n stackable-airflow --controller-namespace sealed-secrets --scope=cluster-wide --format=yaml < "$input_file" > "$output_file"
    done

build-airflow-image:
    docker build -t oci.stackable.tech/sandbox/airflow:3.1.6-stackable0.0.0-dev-cosmos docker/airflow
    docker push oci.stackable.tech/sandbox/airflow:3.1.6-stackable0.0.0-dev-cosmos

infra name="":
    #!/usr/bin/env bash
    cd tofu
    tofu init -upgrade
    if [ -n "{{name}}" ]; then
        tofu apply -var="name={{name}}"
    else
        tofu apply
    fi

destroy:
    #!/usr/bin/env bash
    cd tofu
    tofu destroy

kubeconfig name:
    #!/usr/bin/env bash
    cd tofu
    RG=$(tofu output -raw resource_group_name)
    az aks get-credentials --resource-group "$RG" --name "{{name}}" --overwrite-existing

dbt-compile:
    #!/usr/bin/env bash
    cd dags/dbt/tpch_demo
    dbt compile --profiles-dir .

dbt-run:
    #!/usr/bin/env bash
    cd dags/dbt/tpch_demo
    dbt run --profiles-dir .
