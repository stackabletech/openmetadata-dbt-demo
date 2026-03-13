select
    suppkey as supplier_id,
    name as supplier_name,
    address as supplier_address,
    nationkey as nation_id,
    phone as supplier_phone,
    acctbal as account_balance,
    comment as supplier_comment
from {{ source('tpch', 'supplier') }}
