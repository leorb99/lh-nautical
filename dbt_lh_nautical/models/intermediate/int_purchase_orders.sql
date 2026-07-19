with
    purchase_orders as (
        select *
        from {{ ref('stg_purchase_orders') }}
    )

    , cleaned_purchase_orders as (
        select
            purchase_order_id
            , purchase_order_number
            , supplier_id
            , buyer_id
            , destination_location_id
            , purchase_order_status
            , purchase_order_currency
            , purchase_order_subtotal
            , purchase_order_total

            -- Nullifies future timestamps confirmed via discovery (~14% of rows,
            -- a purchase order cannot have been placed/created/updated in the future)
            , case when placed_at > current_timestamp() then null else placed_at end as placed_at
            , case when created_at > current_timestamp() then null else created_at end as created_at
            , case when updated_at > current_timestamp() then null else updated_at end as updated_at

            , purchase_order_expected_delivery_at
        from purchase_orders
    )

select *
from cleaned_purchase_orders
