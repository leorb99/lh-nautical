with
    source as (
        select * 
        from {{ source('bronze', 'purchase_orders') }}
    )

    , purchase_orders as (
        select
            cast(id as int) as purchase_orders_id
            , cast(po_number as string) as purchase_order_number
            , cast(supplier_id as int) as supplier_id
            , cast(buyer_id as int) as buyer_id
            , cast(destination_location_id as string) as destination_location_id
            , cast(status as string) as purchase_order_status
            , cast(currency as string) as currency
            , cast(subtotal as decimal(14,2)) as purchase_order_subtotal
            , cast(total as decimal(14,2)) as purchase_order_total
            , cast(placed_at as timestamp) as placed_at
            , cast(expected_delivery_at as date) as purchase_order_expected_delivery_at
            , cast(created_at as timestamp) as created_at
            , cast(updated_at as timestamp) as updated_at

            -- internal ingestion metadata
            , cast(_hash as string) as _hash
            , cast(_ingested_at as timestamp) as _ingested_at
            , cast(_action as string) as _action
        from source
    )
select * 
from purchase_orders