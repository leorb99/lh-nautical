with
    current_inventory as (
        select
            product_variant_id
            , location_id
        from {{ ref('int_current_inventory') }}
    )

    , replenishment_inputs as (
        select
            product_variant_id
            , location_id
        from {{ ref('int_inventory_replenishment_inputs') }}
    )

select
    current_inventory.product_variant_id
    , current_inventory.location_id

from current_inventory

left join replenishment_inputs
    on current_inventory.product_variant_id
    = replenishment_inputs.product_variant_id
    and current_inventory.location_id
    = replenishment_inputs.location_id

where replenishment_inputs.product_variant_id is null
