MODEL (
  name demo_sqlmesh.stg_part_suppliers,
  kind VIEW,
  description 'Cleaned part-supplier records. One row per part-supplier combination.',
  column_descriptions (
    part_id              = 'Foreign key to stg_parts.',
    supplier_id          = 'Foreign key to stg_suppliers.',
    available_quantity   = 'Units available from this supplier for this part.',
    supply_cost          = 'Cost per unit from this supplier, in dollars.',
    partsupplier_comment = 'Free-text comment about this part-supplier relationship.'
  )
);

select
    partkey as part_id,
    suppkey as supplier_id,
    availqty as available_quantity,
    supplycost as supply_cost,
    comment as partsupplier_comment
from tpch.tiny.partsupp
