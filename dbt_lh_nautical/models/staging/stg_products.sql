with
    source as (
        select *
        from {{ source('bronze', 'products') }}
    )

    , products as (
        select
            cast(id as int) as product_id
            , cast(name as string) as product_name
            , cast(description as string) as product_description
            , cast(brand_id as int) as brand_id
            , cast(category_id as int) as category_id
            , cast(ncm_code as string) as product_ncm_code
            , cast(unit_of_measure as string) as product_unit_of_measure
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
from products
