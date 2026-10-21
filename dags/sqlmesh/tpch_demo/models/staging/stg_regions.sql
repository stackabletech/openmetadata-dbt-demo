MODEL (
  name demo_sqlmesh.stg_regions,
  kind VIEW,
  description 'Cleaned region records. One row per region (5 regions).',
  column_descriptions (
    region_id      = 'Unique region identifier.',
    region_name    = 'Region name (AFRICA, AMERICA, ASIA, EUROPE, MIDDLE EAST).',
    region_comment = 'Free-text comment about the region.'
  )
);

select
    regionkey as region_id,
    name as region_name,
    comment as region_comment
from tpch.tiny.region
