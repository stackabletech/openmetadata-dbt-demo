-- Demo schema in the Lakekeeper-backed catalog. Lakekeeper manages the table
-- locations (s3://iceberg-warehouse/data2day/...), so no location here.
CREATE SCHEMA IF NOT EXISTS data2day.demo;
