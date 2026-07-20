with
    customers as (
        select *
        from {{ ref('int_customers') }}
    )

    , selected_customers as (
        select
            {{ dbt_utils.generate_surrogate_key(['customer_id']) }} as customer_sk
            , customer_id
            , customer_person_type
            , customer_legal_name
            , customer_trade_name
            , customer_tax_id
            , customer_state_registration
            , customer_email
            , customer_phone
            , is_active
            , created_at

            -- updated_at is intentionally excluded: it's a source audit timestamp,
            -- not used by any business logic and with no clear analytical value here
            -- , updated_at

            -- primary_address_id is intentionally excluded: every useful address
            -- attribute is already denormalized below, so it's only a technical
            -- reference with no analytical use
            -- , primary_address_id

            , primary_address_type
            , primary_address_postal_code
            , primary_address_street_name
            , primary_address_number
            , primary_address_complement
            , primary_address_district
            , primary_address_city
            , primary_address_state
            , primary_address_country
        from customers
    )

select *
from selected_customers
