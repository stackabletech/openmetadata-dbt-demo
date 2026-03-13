select
    r.region_name,
    n.nation_name,
    count(distinct c.customer_id) as customer_count,
    count(distinct o.order_id) as order_count,
    sum(li.extended_price * (1 - li.discount)) as net_revenue,
    avg(o.total_price) as avg_order_value
from {{ ref('stg_regions') }} r
join {{ ref('stg_nations') }} n on r.region_id = n.region_id
join {{ ref('stg_customers') }} c on n.nation_id = c.nation_id
join {{ ref('stg_orders') }} o on c.customer_id = o.customer_id
join {{ ref('stg_line_items') }} li on o.order_id = li.order_id
group by
    r.region_name,
    n.nation_name
