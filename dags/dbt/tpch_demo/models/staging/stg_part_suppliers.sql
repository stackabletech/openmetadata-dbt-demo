select
    partkey as part_id,
    suppkey as supplier_id,
    availqty as available_quantity,
    supplycost as supply_cost,
    comment as partsupplier_comment
from {{ source('tpch', 'partsupp') }}
