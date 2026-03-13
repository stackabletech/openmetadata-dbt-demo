select
    nationkey as nation_id,
    name as nation_name,
    regionkey as region_id,
    comment as nation_comment
from {{ source('tpch', 'nation') }}
