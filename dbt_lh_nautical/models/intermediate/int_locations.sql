with
    locations as (
        select *
        from {{ ref('stg_locations')}}
    )

    , cleaned_locations as (
        select 
            location_id
            -- Capitalize first letters, remove extra spaces, and keep prepositions in lowercase
            , {{ clean_and_format_text('location_name') }} as location_name
            , lower(location_type)
            
            -- Remove dots and dashes
            , regexp_replace(location_postal_code, '[^0-9]', '') as location_postal_code
            
            -- Remove extra spaces
            , trim(location_street)
            , location_number

            -- Handles '[NULL]' strings or actual nulls to maintain the database NULL standard
            , case 
                when trim(upper(location_complement)) = '[NULL]' or trim(location_complement) = '' then null 
                else trim(location_complement) 
            end as location_complement

            -- 
            , {{ clean_and_format_text("replace(location_district, 'Paulo Vi', 'Paulo VI')") }} as location_district
            , {{ clean_and_format_text('location_city') }} as location_city
            , upper(trim(location_state)) as location_state
            , upper(trim(location_country)) as location_country
            , is_active
            from locations
    )

select *
from cleaned_locations