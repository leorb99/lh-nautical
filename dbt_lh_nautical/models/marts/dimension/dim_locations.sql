with
    locations as (
        select *
        from {{ ref('int_locations') }}
    )

    , selected_locations as (
        select
            {{ dbt_utils.generate_surrogate_key(['location_id']) }} as location_sk
            , location_id
            , location_name
            , location_type
            , location_postal_code
            , location_street
            , location_number
            , location_complement
            , location_district
            , location_city
            , location_state
            , location_country
            , is_active
            , created_at

            -- updated_at is intentionally excluded: it's a source audit timestamp,
            -- not used by any business logic and with no clear analytical value here
            -- , updated_at
        from locations
    )

select *
from selected_locations
