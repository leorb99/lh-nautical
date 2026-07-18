with goods_receipt_items as (
    select *
    from {{ ref('stg_goods_receipt_items') }}
)

, intermediate_transformations as (
    select
        goods_receipt_item_id
        , goods_receipt_id
        , purchase_order_item_id
        
        -- Quantities and volume validations
        , goods_receipt_item_quantity_received
        
        -- Flag to identify if the item has fractional quantities (common for weight/volume measurements)
        , case 
            when goods_receipt_item_quantity_received - floor(goods_receipt_item_quantity_received) > 0 
            then true 
            else false 
          end as is_fractional_quantity
          
        -- Flag to easily audit empty or rejected physical receipts at line level
        , case 
            when goods_receipt_item_quantity_received = 0 
            then true 
            else false 
          end as is_zero_quantity_receipt

    from goods_receipt_items
)

select *
from intermediate_transformations