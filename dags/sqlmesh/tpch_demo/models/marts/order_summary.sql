MODEL (
  name demo_sqlmesh.order_summary,
  kind FULL,
  description 'Enriched order-level summary joining orders with customer, nation, region, and aggregated line item metrics. One row per order.',
  grain order_id,
  audits (not_null(columns := (order_id)), unique_values(columns := (order_id))),
  column_descriptions (
    order_id        = 'Unique order identifier.',
    order_date      = 'Date the order was placed.',
    order_status    = 'Order status: F = fulfilled, O = open, P = partially fulfilled.',
    order_priority  = 'Priority: 1-URGENT, 2-HIGH, 3-MEDIUM, 4-NOT SPECIFIED, 5-LOW.',
    customer_name   = 'Name of the customer who placed the order.',
    market_segment  = 'Market segment of the customer.',
    customer_nation = 'Nation where the customer is located.',
    customer_region = 'Geographic region where the customer is located.',
    total_price     = 'Total order price in dollars as recorded on the order.',
    line_item_count = 'Number of line items in the order.',
    total_quantity  = 'Sum of quantities across all line items.',
    gross_revenue   = 'Sum of extended prices before discounts.',
    net_revenue     = 'Revenue after discount. sum(extended_price * (1 - discount)).',
    total_with_tax  = 'Net revenue plus tax. sum(extended_price * (1 - discount) * (1 + tax)).'
  )
);

select
    o.order_id,
    o.order_date,
    o.order_status,
    o.order_priority,
    c.customer_name,
    c.market_segment,
    n.nation_name as customer_nation,
    r.region_name as customer_region,
    o.total_price,
    count(li.line_number) as line_item_count,
    sum(li.quantity) as total_quantity,
    sum(li.extended_price) as gross_revenue,
    sum(li.extended_price * (1 - li.discount)) as net_revenue,
    sum(li.extended_price * (1 - li.discount) * (1 + li.tax)) as total_with_tax
from demo_sqlmesh.stg_orders o
join demo_sqlmesh.stg_customers c on o.customer_id = c.customer_id
join demo_sqlmesh.stg_nations n on c.nation_id = n.nation_id
join demo_sqlmesh.stg_regions r on n.region_id = r.region_id
join demo_sqlmesh.stg_line_items li on o.order_id = li.order_id
group by
    o.order_id,
    o.order_date,
    o.order_status,
    o.order_priority,
    c.customer_name,
    c.market_segment,
    n.nation_name,
    r.region_name,
    o.total_price
