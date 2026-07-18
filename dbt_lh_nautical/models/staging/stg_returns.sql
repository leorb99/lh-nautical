with
    source as (
        select *
        from {{ source('bronze', 'returns') }}
    )

    , returns as (
        select
            cast(id as int) as return_id
            , cast(return_number as string) as return_number
            , cast(order_id as int) as order_id
            , cast(customer_id as int) as customer_id
            , cast(received_at_location_id as int) as received_at_location_id
            , cast(status as string) as return_status
            , cast(reason as string) as return_reason
            , cast(total_refund_amount as decimal(14, 2)) as return_total_refund_amount
            , cast(created_at as timestamp) as created_at
            , cast(updated_at as timestamp) as updated_at

            -- internal ingestion metadata
            , cast(_hash as string) as _hash
            , cast(_ingested_at as timestamp) as _ingested_at
            , cast(_action as string) as _action
        from source
    )

select *
from returns