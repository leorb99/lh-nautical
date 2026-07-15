with 
    order_items as (
        select *
        from {{ source('bronze', 'order_items') }}
    )

    , renamed as (
        select
            cast(id as int) as order_item_id
            , cast(order_id as int) as order_id
            , cast(product_variant_id as int) as product_variant_id
            , cast(quantity as numeric) as order_item_quantity
            , cast(unit_price as numeric) as order_item_unit_price
            , cast(icms_rate as numeric) as order_item_icms_rate
            , cast(ipi_rate as numeric) as order_item_ipi_rate
            , cast(line_total as numeric) as order_item_line_total

            -- internal ingestion metadata
            , cast(_hash as string) as _hash
            , cast(_ingested_at as timestamp) as _ingested_at
            , cast(_action as string) as _action
        from order_items
    )

select *
from renamed