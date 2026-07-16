with 
    orders as (
        select * 
        from {{ source('bronze', 'orders') }}
    )
    
    , renamed as (
        select
            cast(id as int) as order_id
            , cast(customer_id as int) as customer_id
            , cast(salesperson_id as int) as salesperson_id
            , cast(location_id as int) as location_id
            , cast(order_number as string) as order_number
            , cast(channel as string) as order_channel
            , cast(status as string) as order_status
            , cast(subtotal as numeric) as order_subtotal_amount
            , cast(discount_amount as numeric) as order_discount_amount
            , cast(total as numeric) as order_total_amount
            , cast(placed_at as timestamp) as placed_at
            , cast(created_at as timestamp) as created_at
            , cast(updated_at as timestamp) as updated_at
            
            
            -- internal ingestion metadata
            , cast(_hash as string) as _hash
            , cast(_ingested_at as timestamp) as _ingested_at
            , cast(_action as string) as _action
        from orders
)

select *
from renamed