MODEL (
  name demo_sqlmesh.revenue_by_region,
  kind FULL,
  description 'Revenue and order metrics aggregated by region and nation. Useful for geographic analysis of sales performance.',
  grain (region_name, nation_name),
  audits (not_null(columns := (region_name, nation_name)), unique_combination_of_columns(columns := (region_name, nation_name))),
  column_descriptions (
    region_name     = 'Geographic region name.',
    nation_name     = 'Nation name within the region.',
    customer_count  = 'Number of distinct customers with orders.',
    order_count     = 'Number of distinct orders.',
    net_revenue     = 'Total revenue after discounts.',
    avg_order_value = 'Average total_price per order.'
  )
);

select
    r.region_name,
    n.nation_name,
    count(distinct c.customer_id) as customer_count,
    count(distinct o.order_id) as order_count,
    sum(li.extended_price * (1 - li.discount)) as net_revenue,
    avg(o.total_price) as avg_order_value
from demo_sqlmesh.stg_regions r
join demo_sqlmesh.stg_nations n on r.region_id = n.region_id
join demo_sqlmesh.stg_customers c on n.nation_id = c.nation_id
join demo_sqlmesh.stg_orders o on c.customer_id = o.customer_id
join demo_sqlmesh.stg_line_items li on o.order_id = li.order_id
group by
    r.region_name,
    n.nation_name
