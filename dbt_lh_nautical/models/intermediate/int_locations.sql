with
    locations as (
        select *
        from {{ ref('stg_locations') }}
    )

    , cleaned_locations as (
        select
            location_id
            -- Capitalize first letters, remove extra spaces, keep prepositions in lowercase and remove titles
            , {{ clean_and_format_name_place('location_name') }} as location_name
            , lower(location_type) as location_type

            -- Remove dots and dashes
            , regexp_replace(location_postal_code, '[^0-9]', '') as location_postal_code

            , {{ clean_and_format_name_place('location_street') }} as location_street
            , location_number

            -- Handles '[NULL]' strings or actual nulls to maintain the database NULL standard
            , case
                when {{ upper_trim('location_complement') }} = '[NULL]' or trim(location_complement) = '' then null
                else trim(location_complement)
            end as location_complement

            , replace({{ clean_and_format_name_place('location_district') }}, 'Paulo Vi', 'Paulo VI') as location_district
            , {{ clean_and_format_name_place('location_city') }} as location_city
            , {{ upper_trim('location_state') }} as location_state
            , {{ upper_trim('location_country') }} as location_country
            , is_active

            -- Nullifies future timestamps, since a record can't be created/updated ahead
            -- of now; keeps the rest of the row, as this is an isolated field-level issue
            , {{ nullify_future_date(created_at) }} as created_at
            , {{ nullify_future_date(update_at)}} as updated_at
        from locations
    )

select *
from cleaned_locations
