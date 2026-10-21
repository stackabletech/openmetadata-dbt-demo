MODEL (
  name demo_sqlmesh.shipping_analysis,
  kind FULL,
  description 'Shipping and delivery performance metrics aggregated by ship mode, region, and nation. Tracks delivery timeliness, late delivery rates, and return rates.',
  grain (ship_mode, customer_region, customer_nation),
  audits (not_null(columns := (ship_mode, customer_region, customer_nation)), unique_combination_of_columns(columns := (ship_mode, customer_region, customer_nation))),
  column_descriptions (
    ship_mode                  = 'Shipping mode (AIR, FOB, MAIL, RAIL, REG AIR, SHIP, TRUCK).',
    customer_region            = 'Geographic region of the receiving customer.',
    customer_nation            = 'Nation of the receiving customer.',
    shipment_count             = 'Total number of line items shipped in this group.',
    total_quantity_shipped     = 'Total quantity of items shipped.',
    net_revenue                = 'Revenue after discounts for this group.',
    avg_delivery_days          = 'Average days between ship date and receipt date (transit time).',
    avg_receipt_vs_commit_days = 'Average days between committed date and receipt date. Negative = early, positive = late.',
    avg_days_to_ship           = 'Average days between order date and ship date (fulfillment speed).',
    late_deliveries            = 'Number of line items received after the committed date.',
    late_delivery_rate         = 'Fraction of line items delivered late (0.0 to 1.0).',
    returned_items             = 'Number of line items with return_flag = R.',
    return_rate                = 'Fraction of line items returned (0.0 to 1.0).'
  )
);

select
    li.ship_mode,
    r.region_name as customer_region,
    n.nation_name as customer_nation,
    count(*) as shipment_count,
    sum(li.quantity) as total_quantity_shipped,
    sum(li.extended_price * (1 - li.discount)) as net_revenue,
    avg(date_diff('day', li.ship_date, li.receipt_date)) as avg_delivery_days,
    avg(date_diff('day', li.commit_date, li.receipt_date)) as avg_receipt_vs_commit_days,
    avg(date_diff('day', o.order_date, li.ship_date)) as avg_days_to_ship,
    sum(case when li.receipt_date > li.commit_date then 1 else 0 end) as late_deliveries,
    cast(sum(case when li.receipt_date > li.commit_date then 1 else 0 end) as double)
        / count(*) as late_delivery_rate,
    sum(case when li.return_flag = 'R' then 1 else 0 end) as returned_items,
    cast(sum(case when li.return_flag = 'R' then 1 else 0 end) as double)
        / count(*) as return_rate
from demo_sqlmesh.stg_line_items li
join demo_sqlmesh.stg_orders o on li.order_id = o.order_id
join demo_sqlmesh.stg_customers c on o.customer_id = c.customer_id
join demo_sqlmesh.stg_nations n on c.nation_id = n.nation_id
join demo_sqlmesh.stg_regions r on n.region_id = r.region_id
group by
    li.ship_mode,
    r.region_name,
    n.nation_name
