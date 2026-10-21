MODEL (
  name demo_sqlmesh.part_pricing_analysis,
  kind FULL,
  description 'Product pricing and supply chain metrics aggregated by part type, brand, manufacturer, and container. Combines catalog pricing with sales data and supply costs.',
  grain (part_type, brand, manufacturer, container),
  audits (not_null(columns := (part_type, brand, manufacturer, container)), unique_combination_of_columns(columns := (part_type, brand, manufacturer, container))),
  column_descriptions (
    part_type                 = 'Part type category (e.g. STANDARD POLISHED BRASS).',
    brand                     = 'Brand name (format Brand#NN).',
    manufacturer              = 'Manufacturer name (format Manufacturer#N).',
    container                 = 'Shipping container type (e.g. SM CASE, LG BOX).',
    part_count                = 'Number of distinct parts in this category.',
    avg_retail_price          = 'Average retail price across parts.',
    min_retail_price          = 'Lowest retail price.',
    max_retail_price          = 'Highest retail price.',
    supplier_count            = 'Number of distinct suppliers for this category.',
    avg_supply_cost           = 'Average cost per unit from suppliers.',
    avg_margin                = 'Average retail price minus supply cost (gross margin per unit).',
    total_available_inventory = 'Total units available across all suppliers.',
    total_quantity_sold       = 'Total units sold. Zero if no sales.',
    total_net_revenue         = 'Total revenue after discounts. Zero if no sales.',
    avg_discount_applied      = 'Average discount rate on line items in this category.'
  )
);

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
from demo_sqlmesh.stg_parts p
join demo_sqlmesh.stg_part_suppliers ps on p.part_id = ps.part_id
left join demo_sqlmesh.stg_line_items li
    on p.part_id = li.part_id
    and ps.supplier_id = li.supplier_id
group by
    p.part_type,
    p.brand,
    p.manufacturer,
    p.container
