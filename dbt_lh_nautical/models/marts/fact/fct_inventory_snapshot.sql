with
    inventory_snapshots as (
        select *
        from {{ ref('int_inventory_snapshots') }}
    )

select *
from inventory_snapshots
