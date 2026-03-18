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
        if [ -f "$output_file" ]; then
            echo "Skipping (already exists): $output_file"
            continue
        fi
        echo "Processing: $input_file -> $output_file"
        kubeseal -n stackable-airflow --controller-namespace sealed-secrets --scope=cluster-wide --format=yaml < "$input_file" > "$output_file"
    done

build-airflow-image:
    docker build -t oci.stackable.tech/sandbox/airflow:3.1.6-stackable0.0.0-dev-cosmos docker/airflow
    docker push oci.stackable.tech/sandbox/airflow:3.1.6-stackable0.0.0-dev-cosmos

[private]
_select-cluster:
    #!/usr/bin/env bash
    cd tofu
    states=(*.tfstate)
    if [ ! -e "${states[0]}" ]; then
        echo "No state files found in tofu/" >&2
        exit 1
    fi
    declare -a cluster_names
    for sf in "${states[@]}"; do
        cluster_names+=("${sf%.tfstate}")
    done
    if command -v rofi &>/dev/null; then
        selection=$(printf '%s\n' "${cluster_names[@]}" | rofi -dmenu -p "Select cluster")
        if [ -z "$selection" ]; then
            echo "No cluster selected" >&2
            exit 1
        fi
        echo "$selection"
    else
        echo "Select a cluster:" >&2
        for i in "${!cluster_names[@]}"; do
            echo "  $((i+1))) ${cluster_names[$i]}" >&2
        done
        read -rp "Choice [1-${#cluster_names[@]}]: " choice </dev/tty >&2
        if [ -z "$choice" ] || [ "$choice" -lt 1 ] || [ "$choice" -gt "${#cluster_names[@]}" ] 2>/dev/null; then
            echo "Invalid selection" >&2
            exit 1
        fi
        echo "${cluster_names[$((choice-1))]}"
    fi

infra name:
    #!/usr/bin/env bash
    cd tofu
    tofu init -upgrade
    tofu apply -auto-approve -var="name={{name}}" -state="{{name}}.tfstate"

destroy name="":
    #!/usr/bin/env bash
    if [ -n "{{name}}" ]; then
        CLUSTER="{{name}}"
    else
        CLUSTER=$(just _select-cluster)
    fi
    cd tofu
    RG=$(tofu output -raw -state="${CLUSTER}.tfstate" resource_group_name)
    echo "Deleting resource group '$RG' (async)..."
    az group delete --name "$RG" --yes --no-wait
    echo "Cleaning up state file '${CLUSTER}.tfstate'..."
    rm -f "${CLUSTER}.tfstate" "${CLUSTER}.tfstate.backup"

kubeconfig name="":
    #!/usr/bin/env bash
    if [ -n "{{name}}" ]; then
        CLUSTER="{{name}}"
    else
        CLUSTER=$(just _select-cluster)
    fi
    cd tofu
    RG=$(tofu output -raw -state="${CLUSTER}.tfstate" resource_group_name)
    az aks get-credentials --resource-group "$RG" --name "${CLUSTER}" --overwrite-existing

dbt-compile:
    #!/usr/bin/env bash
    cd dags/dbt/tpch_demo
    dbt compile --profiles-dir .

dbt-run:
    #!/usr/bin/env bash
    cd dags/dbt/tpch_demo
    dbt run --profiles-dir .
