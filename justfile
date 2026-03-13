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

dbt-compile:
    #!/usr/bin/env bash
    cd dags/dbt/tpch_demo
    dbt compile --profiles-dir .

dbt-run:
    #!/usr/bin/env bash
    cd dags/dbt/tpch_demo
    dbt run --profiles-dir .
