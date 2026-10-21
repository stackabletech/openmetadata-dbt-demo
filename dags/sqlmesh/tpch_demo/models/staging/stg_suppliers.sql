MODEL (
  name demo_sqlmesh.stg_suppliers,
  kind VIEW,
  description 'Cleaned supplier records. One row per supplier.',
  column_descriptions (
    supplier_id      = 'Unique supplier identifier.',
    supplier_name    = 'Supplier name (format Supplier#NNNNNNNNN).',
    supplier_address = 'Street address of the supplier.',
    nation_id        = 'Foreign key to stg_nations.',
    supplier_phone   = 'Phone number (format NN-NNN-NNN-NNNN).',
    account_balance  = 'Account balance in dollars.',
    supplier_comment = 'Free-text comment about the supplier.'
  )
);

select
    suppkey as supplier_id,
    name as supplier_name,
    address as supplier_address,
    nationkey as nation_id,
    phone as supplier_phone,
    acctbal as account_balance,
    comment as supplier_comment
from tpch.tiny.supplier
