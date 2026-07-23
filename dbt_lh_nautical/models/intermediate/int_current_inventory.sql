with
    inventory_snapshots as (
        select *
        from {{ ref('int_inventory_snapshots') }}
        where is_current_snapshot = true
    )

    , current_inventory as (
        select
            inventory_snapshot_sk as current_inventory_snapshot_sk

            , product_variant_id
            , location_id
            , quantity_on_hand
            , reorder_point
            , source_updated_at
            , snapshot_valid_from
            , snapshot_date
            , is_out_of_stock
            , is_below_reorder_point

        from inventory_snapshots
    )

select *
from current_inventory
