select
    regionkey as region_id,
    name as region_name,
    comment as region_comment
from {{ source('tpch', 'region') }}
