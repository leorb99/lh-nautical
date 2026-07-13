with 
    addresses as (
        select
        cast(id as int) as address_id
        , cast(customer_id as int) as customer_id
        , cast(address_type as string) as address_type
        , cast(postal_code as string) as postal_code
        , cast(street as string) as street_name
        , cast(number as string) as address_number
        , cast(complement as string) as address_complement
        , cast(district as string) as district
        , cast(city as string) as city
        , cast(state as string) as state
        , cast(country as string) as country
        , cast(is_primary as boolean) as is_primary
        , cast(_hash as string) as _hash
        , cast(_ingested_at as timestamp) as _ingested_at
        , cast(_action as string) as _action
    from {{ source('bronze', 'addresses') }}
    )

select *
from addresses