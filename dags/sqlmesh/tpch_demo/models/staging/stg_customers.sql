MODEL (
  name demo_sqlmesh.stg_customers,
  kind VIEW,
  description 'Cleaned customer records. One row per customer.',
  column_descriptions (
    customer_id      = 'Unique customer identifier.',
    customer_name    = 'Customer name (format Customer#NNNNNNNNN).',
    customer_address = 'Street address of the customer.',
    nation_id        = 'Foreign key to stg_nations.',
    customer_phone   = 'Phone number (format NN-NNN-NNN-NNNN).',
    account_balance  = 'Account balance in dollars. May be negative.',
    market_segment   = 'Market segment (AUTOMOBILE, BUILDING, FURNITURE, HOUSEHOLD, MACHINERY).',
    customer_comment = 'Free-text comment about the customer.'
  )
);

select
    custkey as customer_id,
    name as customer_name,
    address as customer_address,
    nationkey as nation_id,
    phone as customer_phone,
    acctbal as account_balance,
    mktsegment as market_segment,
    comment as customer_comment
from tpch.tiny.customer
