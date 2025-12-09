#!/usr/bin/env bash

# Replicate directory structure from 'secrets' to 'platform'
find secrets -type d | while read -r dir; do
    # Replace 'secrets' prefix with 'platform'
    target_dir="${dir/secrets/platform}"
    echo "Creating $target_dir"
    mkdir -p "$target_dir"
done

# Process all yaml files
find secrets -type f \( -name "*.yaml" -o -name "*.yml" \) | while read -r input_file; do
    # Replace 'secrets' prefix with 'platform' for output path
    output_path="${input_file/secrets/platform}"

    # Extract directory and filename, then add "sealed-" prefix
    output_dir="$(dirname "$output_path")"
    output_filename="sealed-$(basename "$output_path")"
    output_file="$output_dir/$output_filename"

    echo "Processing: $input_file -> $output_file"
    kubeseal -n stackable-airflow --controller-namespace sealed-secrets --scope=cluster-wide --format=yaml < "$input_file" > "$output_file"
done