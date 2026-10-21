MODEL (
  name demo_sqlmesh.stg_orders,
  kind VIEW,
  description 'Cleaned order records. One row per order.',
  column_descriptions (
    order_id       = 'Unique order identifier.',
    customer_id    = 'Foreign key to stg_customers.',
    order_status   = 'Order status: F = fulfilled, O = open, P = partially fulfilled.',
    total_price    = 'Total order price in dollars.',
    order_date     = 'Date the order was placed.',
    order_priority = 'Priority: 1-URGENT, 2-HIGH, 3-MEDIUM, 4-NOT SPECIFIED, 5-LOW.',
    clerk          = 'Clerk who processed the order.',
    ship_priority  = 'Shipping priority flag (integer).',
    order_comment  = 'Free-text comment about the order.'
  )
);

select
    orderkey as order_id,
    custkey as customer_id,
    orderstatus as order_status,
    totalprice as total_price,
    orderdate as order_date,
    orderpriority as order_priority,
    clerk,
    shippriority as ship_priority,
    comment as order_comment
from tpch.tiny.orders
