with
    attributes as (
        select *
        from {{ source('bronze', 'attributes') }}
    )

    , renamed as (
        select
            cast(id as int) as attribute_id
            , cast(name as string) as attribute_name
            , cast(data_type as string) as attribute_data_type

            -- internal ingestion metadata
            , cast(_hash as string) as _hash
            , cast(_ingested_at as timestamp) as _ingested_at
            , cast(_action as string) as _action
        from attributes
    )

select *
from renamed
