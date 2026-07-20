with
    source as (
        select *
        from {{ source('bronze', 'payments') }}
    )

    , payments as (
        select
            cast(id as int) as payment_id
            , cast(order_id as int) as order_id
            , cast(method as string) as payment_method
            , cast(installments as int) as payment_installments
            , cast(amount as decimal(14, 2)) as payment_amount
            , cast(status as string) as payment_status
            , cast(paid_at as timestamp) as paid_at
            , cast(created_at as timestamp) as created_at
            , cast(updated_at as timestamp) as updated_at

            -- internal ingestion metadata
            , cast(_hash as string) as _hash
            , cast(_ingested_at as timestamp) as _ingested_at
            , cast(_action as string) as _action
        from source
    )

select *
from payments
