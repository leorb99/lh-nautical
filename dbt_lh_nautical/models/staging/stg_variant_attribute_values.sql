with
    source as (
        select *
        from {{ source('bronze', 'variant_attribute_values') }}
    )

    , variant_attribute_values as (
        select
            cast(product_variant_id as int) as product_variant_id
            , cast(attribute_id as int) as attribute_id
            , trim(cast(value as string)) as attribute_value

            -- internal ingestion metadata
            , cast(_hash as string) as _hash
            , cast(_ingested_at as timestamp) as _ingested_at
            , cast(_action as string) as _action
        from source
    )

select *
from variant_attribute_values