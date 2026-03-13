CREATE TABLE IF NOT EXISTS hive.demo.example (
    id BIGINT,
    name VARCHAR,
    created_at TIMESTAMP
) WITH (
    format = 'parquet'
);
