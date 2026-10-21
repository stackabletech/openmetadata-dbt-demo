MODEL (
  name demo_sqlmesh.customer_lifetime_value,
  kind FULL,
  description 'Customer-level lifetime value metrics including total spend, order frequency, and product diversity. One row per customer. Includes customers with zero orders.',
  grain customer_id,
  audits (not_null(columns := (customer_id)), unique_values(columns := (customer_id))),
  column_descriptions (
    customer_id             = 'Unique customer identifier.',
    customer_name           = 'Name of the customer.',
    market_segment          = 'Market segment the customer belongs to.',
    customer_nation         = 'Nation where the customer is located.',
    customer_region         = 'Geographic region where the customer is located.',
    account_balance         = 'Current account balance in dollars. May be negative.',
    total_orders            = 'Total number of orders placed by this customer.',
    first_order_date        = 'Date of the customer''s earliest order.',
    last_order_date         = 'Date of the customer''s most recent order.',
    lifetime_net_revenue    = 'Total revenue after discounts across all orders.',
    lifetime_quantity       = 'Total quantity of items ordered.',
    avg_order_value         = 'Average total_price per order.',
    revenue_per_order       = 'Lifetime net revenue divided by total orders.',
    total_line_items        = 'Total number of line items across all orders.',
    distinct_parts_ordered  = 'Number of unique parts this customer has ordered.',
    distinct_suppliers_used = 'Number of unique suppliers that fulfilled this customer''s orders.'
  )
);

select
    c.customer_id,
    c.customer_name,
    c.market_segment,
    n.nation_name as customer_nation,
    r.region_name as customer_region,
    c.account_balance,
    count(distinct o.order_id) as total_orders,
    min(o.order_date) as first_order_date,
    max(o.order_date) as last_order_date,
    sum(li.extended_price * (1 - li.discount)) as lifetime_net_revenue,
    sum(li.quantity) as lifetime_quantity,
    avg(o.total_price) as avg_order_value,
    sum(li.extended_price * (1 - li.discount))
        / nullif(count(distinct o.order_id), 0) as revenue_per_order,
    count(li.line_number) as total_line_items,
    count(distinct li.part_id) as distinct_parts_ordered,
    count(distinct li.supplier_id) as distinct_suppliers_used
from demo_sqlmesh.stg_customers c
join demo_sqlmesh.stg_nations n on c.nation_id = n.nation_id
join demo_sqlmesh.stg_regions r on n.region_id = r.region_id
left join demo_sqlmesh.stg_orders o on c.customer_id = o.customer_id
left join demo_sqlmesh.stg_line_items li on o.order_id = li.order_id
group by
    c.customer_id,
    c.customer_name,
    c.market_segment,
    n.nation_name,
    r.region_name,
    c.account_balance
