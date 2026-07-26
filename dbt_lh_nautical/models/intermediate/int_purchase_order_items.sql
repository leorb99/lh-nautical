with
    purchase_order_items as (
        select *
        from {{ ref('stg_purchase_order_items') }}
    )

    , selected_purchase_order_items as (
        select
            purchase_order_item_id
            , purchase_order_id
            , product_variant_id
            , purchase_order_item_quantity_ordered
            , purchase_order_item_unit_cost
            , purchase_order_item_line_total
            -- , _hash
            -- , _ingested_at
            -- , _action
        from purchase_order_items
    )

select *
from selected_purchase_order_items
