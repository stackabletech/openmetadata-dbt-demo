MODEL (
  name demo_sqlmesh.stg_nations,
  kind VIEW,
  description 'Cleaned nation records. One row per nation (25 nations).',
  column_descriptions (
    nation_id      = 'Unique nation identifier.',
    nation_name    = 'Nation name (e.g. UNITED STATES, GERMANY, CHINA).',
    region_id      = 'Foreign key to stg_regions.',
    nation_comment = 'Free-text comment about the nation.'
  )
);

select
    nationkey as nation_id,
    name as nation_name,
    regionkey as region_id,
    comment as nation_comment
from tpch.tiny.nation
