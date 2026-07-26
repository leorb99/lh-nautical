with
    addresses as (
        select *
        from {{ source('bronze', 'addresses') }}
    )

    , renamed as (
        select
            cast(id as int) as address_id
            , cast(customer_id as int) as customer_id
            , cast(address_type as string) as address_type
            , cast(postal_code as string) as address_postal_code
            , cast(street as string) as address_street_name
            , cast(number as string) as address_number
            , cast(complement as string) as address_complement
            , cast(district as string) as address_district
            , cast(city as string) as address_city
            , cast(state as string) as address_state
            , cast(country as string) as address_country
            , cast(is_primary as boolean) as address_is_primary

            -- internal ingestion metadata
            , cast(_hash as string) as _hash
            , cast(_ingested_at as timestamp) as _ingested_at
            , cast(_action as string) as _action
        from addresses
    )

select *
from renamed
