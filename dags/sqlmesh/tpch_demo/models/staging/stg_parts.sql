MODEL (
  name demo_sqlmesh.stg_parts,
  kind VIEW,
  description 'Cleaned part records. One row per part.',
  column_descriptions (
    part_id      = 'Unique part identifier.',
    part_name    = 'Part name composed of randomly generated words.',
    manufacturer = 'Manufacturer name (format Manufacturer#N).',
    brand        = 'Brand name (format Brand#NN).',
    part_type    = 'Part type as a 3-word compound (e.g. STANDARD POLISHED BRASS).',
    part_size    = 'Part size as an integer from 1 to 50.',
    container    = 'Shipping container type (e.g. SM CASE, LG BOX, MED PKG).',
    retail_price = 'Retail price per unit in dollars.',
    part_comment = 'Free-text comment about the part.'
  )
);

select
    partkey as part_id,
    name as part_name,
    mfgr as manufacturer,
    brand,
    type as part_type,
    size as part_size,
    container,
    retailprice as retail_price,
    comment as part_comment
from tpch.tiny.part
