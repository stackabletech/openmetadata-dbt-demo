select
    orderkey as order_id,
    linenumber as line_number,
    partkey as part_id,
    suppkey as supplier_id,
    quantity,
    extendedprice as extended_price,
    discount,
    tax,
    returnflag as return_flag,
    linestatus as line_status,
    shipdate as ship_date,
    commitdate as commit_date,
    receiptdate as receipt_date,
    shipinstruct as ship_instructions,
    shipmode as ship_mode,
    comment as line_item_comment
from {{ source('tpch', 'lineitem') }}
