with
    goods_receipt_items as (
        select *
        from {{ source('bronze', 'goods_receipt_items')}}
    )

    , renamed as (
        select
            cast(id as int) as goods_receipt_item_id
            , cast(goods_receipt_id as int) as goods_receipt_id
            , cast(purchase_order_item_id as int) as purchase_order_item_id
            , cast(quantity_received as decimal(14,3)) as goods_receipt_item_quantity_received

            -- internal ingestion metadata
            , cast(_hash as string) as _hash
            , cast(_ingested_at as timestamp) as _ingested_at
            , cast(_action as string) as _action
        from goods_receipt_items
    )

select *
from renamed