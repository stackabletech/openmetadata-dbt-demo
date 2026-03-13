select
    s.supplier_id,
    s.supplier_name,
    n.nation_name as supplier_nation,
    r.region_name as supplier_region,
    count(distinct li.order_id) as orders_fulfilled,
    sum(li.quantity) as total_quantity_supplied,
    sum(li.extended_price) as total_revenue,
    sum(li.extended_price * (1 - li.discount)) as net_revenue,
    avg(li.extended_price) as avg_item_price,
    min(ps.supply_cost) as min_supply_cost,
    avg(ps.supply_cost) as avg_supply_cost,
    sum(ps.available_quantity) as total_available_quantity
from {{ ref('stg_suppliers') }} s
join {{ ref('stg_nations') }} n on s.nation_id = n.nation_id
join {{ ref('stg_regions') }} r on n.region_id = r.region_id
join {{ ref('stg_line_items') }} li on s.supplier_id = li.supplier_id
join {{ ref('stg_part_suppliers') }} ps
    on s.supplier_id = ps.supplier_id
    and li.part_id = ps.part_id
group by
    s.supplier_id,
    s.supplier_name,
    n.nation_name,
    r.region_name
