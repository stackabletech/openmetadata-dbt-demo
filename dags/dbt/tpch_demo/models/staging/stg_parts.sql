select
    partkey as part_id,
    name as part_name,
    mfgr as manufacturer,
    brand,
    type as part_type,
    size as part_size,
    container,
    retailprice as retail_price,
    comment as part_comment
from {{ source('tpch', 'part') }}
