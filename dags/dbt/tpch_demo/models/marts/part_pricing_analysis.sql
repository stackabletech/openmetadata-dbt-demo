select
    p.part_type,
    p.brand,
    p.manufacturer,
    p.container,
    count(distinct p.part_id) as part_count,
    avg(p.retail_price) as avg_retail_price,
    min(p.retail_price) as min_retail_price,
    max(p.retail_price) as max_retail_price,
    count(distinct ps.supplier_id) as supplier_count,
    avg(ps.supply_cost) as avg_supply_cost,
    avg(p.retail_price - ps.supply_cost) as avg_margin,
    sum(ps.available_quantity) as total_available_inventory,
    coalesce(sum(li.quantity), 0) as total_quantity_sold,
    coalesce(sum(li.extended_price * (1 - li.discount)), 0) as total_net_revenue,
    coalesce(avg(li.discount), 0) as avg_discount_applied
from {{ ref('stg_parts') }} p
join {{ ref('stg_part_suppliers') }} ps on p.part_id = ps.part_id
left join {{ ref('stg_line_items') }} li
    on p.part_id = li.part_id
    and ps.supplier_id = li.supplier_id
group by
    p.part_type,
    p.brand,
    p.manufacturer,
    p.container
