with
    source as (
        select *
        from {{ source('bronze', 'suppliers') }}
    )

    , suppliers as (
        select
            cast(id as int) as supplier_id
            , cast(legal_name as string) as supplier_legal_name
            , cast(trade_name as string) as supplier_trade_name
            , cast(country as string) as supplier_country
            , cast(tax_id as string) as supplier_tax_id
            , cast(tax_id_type as string) as supplier_tax_id_type
            , cast(email as string) as supplier_email
            , cast(phone as string) as supplier_phone
            , cast(contact_name as string) as supplier_contact_name
            , cast(is_active as boolean) as is_active
            , cast(created_at as timestamp) as created_at
            , cast(updated_at as timestamp) as updated_at

            -- internal ingestion metadata
            , cast(_hash as string) as _hash
            , cast(_ingested_at as timestamp) as _ingested_at
            , cast(_action as string) as _action
        from source
    )

select *
from suppliers