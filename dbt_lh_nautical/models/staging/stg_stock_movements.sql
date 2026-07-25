with
    source as (
        select *
        from {{ source('bronze', 'stock_movements') }}
    )

    , stock_movements as (
        select
            cast(id as bigint) as stock_movement_id
            , cast(product_variant_id as int) as product_variant_id
            , cast(location_id as int) as location_id
            , cast(movement_type as string) as stock_movement_type
            , cast(quantity as decimal(14, 3)) as stock_movement_quantity
            , cast(reference_table as string) as stock_movement_reference_table
            , cast(reference_id as int) as stock_movement_reference_id
            , cast(employee_id as int) as employee_id
            , cast(notes as string) as stock_movement_notes
            , cast(occurred_at as timestamp) as occurred_at
            , cast(created_at as timestamp) as created_at

            -- internal ingestion metadata
            , cast(_hash as string) as _hash
            , cast(_ingested_at as timestamp) as _ingested_at
            , cast(_action as string) as _action
        from source
    )

select *
from stock_movements
