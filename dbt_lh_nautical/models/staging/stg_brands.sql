with 
    brands as (
        select *
        from {{ source('bronze', 'brands') }}
    )
    
    , renamed as (
        select
        cast(id as int) as brand_id
        , cast(name as string) as brand_name
        , cast(country as string) as brand_country
        , cast(is_active as boolean) as is_active
        , cast(created_at as timestamp) as created_at
        , cast(updated_at as timestamp) as updated_at

        -- internal ingestion metadata
        , cast(_hash as string) as _hash
        , cast(_ingested_at as timestamp) as _ingested_at
        , cast(_action as string) as _action
    from brands
    )

select *
from renamed