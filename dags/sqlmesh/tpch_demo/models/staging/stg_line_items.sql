MODEL (
  name demo_sqlmesh.stg_line_items,
  kind VIEW,
  description 'Cleaned line item records. One row per line item (order + line number). The most granular fact table.',
  column_descriptions (
    order_id          = 'Foreign key to stg_orders.',
    line_number       = 'Line number within the order (1-based).',
    part_id           = 'Foreign key to stg_parts.',
    supplier_id       = 'Foreign key to stg_suppliers.',
    quantity          = 'Quantity ordered (1 to 50).',
    extended_price    = 'Line item price before discount (quantity * retail price).',
    discount          = 'Discount percentage as a decimal (0.00 to 0.10).',
    tax               = 'Tax rate as a decimal (0.00 to 0.08).',
    return_flag       = 'Return status: R = returned, A = accepted, N = not yet received.',
    line_status       = 'Shipping status: F = shipped, O = not yet shipped.',
    ship_date         = 'Date the line item was shipped.',
    commit_date       = 'Committed delivery date.',
    receipt_date      = 'Actual date the customer received the item.',
    ship_instructions = 'Shipping instruction (DELIVER IN PERSON, COLLECT COD, TAKE BACK RETURN, NONE).',
    ship_mode         = 'Shipping mode (AIR, FOB, MAIL, RAIL, REG AIR, SHIP, TRUCK).',
    line_item_comment = 'Free-text comment about the line item.'
  )
);

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
from tpch.tiny.lineitem
