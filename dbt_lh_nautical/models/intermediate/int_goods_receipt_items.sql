with
    goods_receipt_items as (
        select *
        from {{ ref('stg_goods_receipt_items') }}
    )

    , selected_goods_receipt_items as (
        select
            goods_receipt_item_id
            , goods_receipt_id
            , purchase_order_item_id
            , goods_receipt_item_quantity_received
        from goods_receipt_items
    )

select *
from selected_goods_receipt_items
