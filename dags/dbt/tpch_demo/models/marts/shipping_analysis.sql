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
from {{ ref('stg_line_items') }} li
join {{ ref('stg_orders') }} o on li.order_id = o.order_id
join {{ ref('stg_customers') }} c on o.customer_id = c.customer_id
join {{ ref('stg_nations') }} n on c.nation_id = n.nation_id
join {{ ref('stg_regions') }} r on n.region_id = r.region_id
group by
    li.ship_mode,
    r.region_name,
    n.nation_name
