select
    product_variant_id,
    location_id,
    count(*) as current_snapshot_count

from {{ ref('int_inventory_snapshots') }}

where is_current_snapshot = true

group by
    product_variant_id,
    location_id

having count(*) > 1
