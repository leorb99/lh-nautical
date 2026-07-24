with
    source as (
        select *
        from {{ source('bronze', 'return_items') }}
    )

    , return_items as (
        select
            cast(id as int) as return_item_id
            , cast(return_id as int) as return_id
            , cast(order_item_id as int) as order_item_id
            , cast(quantity as decimal(14, 3)) as return_item_quantity
            , cast(action as string) as return_item_action
            , cast(exchange_variant_id as int) as exchange_variant_id
            , cast(unit_refund_amount as decimal(14, 2)) as return_item_unit_refund_amount

            -- internal ingestion metadata
            , cast(_hash as string) as _hash
            , cast(_ingested_at as timestamp) as _ingested_at
            , cast(_action as string) as _action
        from source
    )

select *
from return_items
