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
from {{ ref('stg_orders') }} o
join {{ ref('stg_customers') }} c on o.customer_id = c.customer_id
join {{ ref('stg_nations') }} n on c.nation_id = n.nation_id
join {{ ref('stg_regions') }} r on n.region_id = r.region_id
join {{ ref('stg_line_items') }} li on o.order_id = li.order_id
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
