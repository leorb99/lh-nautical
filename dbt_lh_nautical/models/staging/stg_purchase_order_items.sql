with
    source as (
        select * 
        from {{ source('bronze', 'purchase_order_items') }}
    )

    , purchase_order_items as (
        select
            cast(id as int) as purchase_order_item_id
            , cast(purchase_order_id as int) as purchase_order_id
            , cast(product_variant_id as int) as product_variant_id
            , cast(quantity_ordered as int) as purchase_order_item_quantity_ordered
            , cast(unit_cost as decimal(14, 2)) as purchase_order_item_unit_cost
            , cast(line_total as decimal(14, 2)) as purchase_order_item_line_total

            -- internal ingestion metadata
            , cast(_hash as string) as _hash
            , cast(_ingested_at as timestamp) as _ingested_at
            , cast(_action as string) as _action
        from source
    )
select * 
from purchase_order_items