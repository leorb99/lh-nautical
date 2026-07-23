with
    source as (
        select *
        from {{ source('bronze', 'stock_levels') }}
    )

    , stock_levels as (
        select
            cast(product_variant_id as int) as product_variant_id
            , cast(location_id as int) as location_id
            , cast(quantity_on_hand as decimal(14, 3)) as stock_level_quantity_on_hand
            , cast(reorder_point as decimal(14, 3)) as stock_level_reorder_point
            , cast(updated_at as timestamp) as updated_at

            -- internal ingestion metadata
            , cast(_hash as string) as _hash
            , cast(_ingested_at as timestamp) as _ingested_at
            , cast(_action as string) as _action
        from source
    )

select *
from stock_levels