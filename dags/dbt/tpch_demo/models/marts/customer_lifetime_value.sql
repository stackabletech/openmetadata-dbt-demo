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
from {{ ref('stg_customers') }} c
join {{ ref('stg_nations') }} n on c.nation_id = n.nation_id
join {{ ref('stg_regions') }} r on n.region_id = r.region_id
left join {{ ref('stg_orders') }} o on c.customer_id = o.customer_id
left join {{ ref('stg_line_items') }} li on o.order_id = li.order_id
group by
    c.customer_id,
    c.customer_name,
    c.market_segment,
    n.nation_name,
    r.region_name,
    c.account_balance
