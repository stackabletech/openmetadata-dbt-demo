select
    custkey as customer_id,
    name as customer_name,
    address as customer_address,
    nationkey as nation_id,
    phone as customer_phone,
    acctbal as account_balance,
    mktsegment as market_segment,
    comment as customer_comment
from {{ source('tpch', 'customer') }}
