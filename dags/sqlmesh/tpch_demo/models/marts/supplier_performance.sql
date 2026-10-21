MODEL (
  name demo_sqlmesh.supplier_performance,
  kind FULL,
  description 'Supplier-level performance metrics including revenue, quantity supplied, and supply cost aggregates. One row per supplier.',
  grain supplier_id,
  audits (not_null(columns := (supplier_id)), unique_values(columns := (supplier_id))),
  column_descriptions (
    supplier_id              = 'Unique supplier identifier.',
    supplier_name            = 'Name of the supplier.',
    supplier_nation          = 'Nation where the supplier is located.',
    supplier_region          = 'Geographic region where the supplier is located.',
    orders_fulfilled         = 'Count of distinct orders this supplier contributed to.',
    total_quantity_supplied  = 'Total quantity of items supplied across all orders.',
    total_revenue            = 'Gross revenue (sum of extended prices).',
    net_revenue              = 'Revenue after discounts.',
    avg_item_price           = 'Average extended price per line item.',
    min_supply_cost          = 'Lowest supply cost across all parts this supplier provides.',
    avg_supply_cost          = 'Average supply cost across all part-supplier relationships.',
    total_available_quantity = 'Total inventory available across all parts.'
  )
);

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
from demo_sqlmesh.stg_suppliers s
join demo_sqlmesh.stg_nations n on s.nation_id = n.nation_id
join demo_sqlmesh.stg_regions r on n.region_id = r.region_id
join demo_sqlmesh.stg_line_items li on s.supplier_id = li.supplier_id
join demo_sqlmesh.stg_part_suppliers ps
    on s.supplier_id = ps.supplier_id
    and li.part_id = ps.part_id
group by
    s.supplier_id,
    s.supplier_name,
    n.nation_name,
    r.region_name
