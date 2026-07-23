with
    locations as (
        select *
        from {{ source('bronze','locations')}}
    )

    , renamed as (
        select
            cast(id as int) as location_id
            , cast(name as string) as location_name
            , cast(location_type as string) as location_type
            , cast(postal_code as string) as location_postal_code
            , cast(street as string) as location_street
            , cast(number as string) as location_number
            , cast(complement as string) as location_complement
            , cast(district as string) as location_district
            , cast(city as string) as location_city
            , cast(state as string) as location_state
            , cast(country as string) as location_country
            , cast(is_active as boolean) as is_active
            , cast(created_at as timestamp) as created_at
            , cast(updated_at as timestamp) as updated_at


            -- internal ingestion metadata
            , cast(_hash as string) as _hash
            , cast(_ingested_at as timestamp) as _ingested_at
            , cast(_action as string) as _action
        from locations
    )

select *
from renamed