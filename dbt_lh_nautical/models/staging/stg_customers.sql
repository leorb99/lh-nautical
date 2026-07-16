with 
    customers as (
        select *
        from {{ source('bronze', 'customers') }}
    )
    
    , renamed as (
        select
        cast(id as int) as customer_id
        , cast(person_type as string) as customer_person_type
        , cast(legal_name as string) as customer_legal_name
        , cast(trade_name as string) as customer_trade_name
        , cast(tax_id as string) as customer_tax_id
        , cast(state_registration as string) as customer_state_registration
        , cast(email as string) as customer_email
        , cast(phone as string) as customer_phone
        , cast(is_active as boolean) as customer_is_active
        , cast(created_at as timestamp) as created_at
        , cast(updated_at as timestamp) as updated_at

        -- internal ingestion metadata
        , cast(_hash as string) as _hash
        , cast(_ingested_at as timestamp) as _ingested_at
        , cast(_action as string) as _action
    from customers
    )

select *
from renamed