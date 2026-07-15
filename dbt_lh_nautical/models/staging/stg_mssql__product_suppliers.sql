with
    source as (
        select * 
        from {{ source('bronze', 'product_suppliers') }}
    )

    , product_suppliers as (
        select
            cast(product_variant_id as int) as product_variant_id
            , cast(supplier_id as int) as supplier_id
            , cast(supplier_sku as string) as supplier_sku
            , cast(last_quoted_cost as decimal(14, 2)) as last_quoted_cost
            , cast(lead_time_days as int) as lead_time_days
            , cast(created_at as timestamp) as created_at
            , cast(updated_at as timestamp) as updated_at

            -- internal ingestion metadata
            , cast(_hash as string) as _hash
            , cast(_ingested_at as timestamp) as _ingested_at
            , cast(_action as string) as _action
        from source
    )
select * 
from product_suppliers