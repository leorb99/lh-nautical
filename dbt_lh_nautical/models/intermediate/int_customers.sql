with
    customers as (
        select *
        from {{ ref('stg_customers') }}
    )

    , addresses as (
        select *
        from {{ ref('stg_addresses')}}
    )

    , primary_addresses_filtered as (
        select
        customer_id
        , address_id as primary_address_id
        , address_type as primary_address_type
        , postal_code as primary_postal_code
        , street_name as primary_street_name
        , address_number as primary_address_number
        , address_complement as primary_address_complement
        , district as primary_district
        , city as primary_city
        , state as primary_state
        , country as primary_country
        -- , is_primary
        -- , _hash
        -- , _ingested_at
        -- , _action
        from addresses
        -- In case there is more than one address marked as primary by mistake,
        -- we're using row_number to ensure only one record per customer (the most recent).
        qualify row_number() over(
            partition by customer_id
            order by is_primary desc, _ingested_at desc
        ) = 1
    )

    , joined as (
        select
            -- customer attributes
            customers.customer_id
            , customers.customer_person_type
            , customers.customer_legal_name
            , customers.customer_trade_name
            , customers.customer_tax_id
            , customers.customer_state_registration
            , customers.customer_email
            , customers.customer_phone
            , customers.is_active
            , customers.created_at
            , customers.updated_at
            -- primary address attributes
            , primary_addresses_filtered.primary_address_id
            -- , primary_addresses_filtered.customer_id
            , primary_addresses_filtered.primary_address_type
            , primary_addresses_filtered.primary_postal_code
            , primary_addresses_filtered.primary_street_name
            , primary_addresses_filtered.primary_address_number
            , primary_addresses_filtered.primary_address_complement
            , primary_addresses_filtered.primary_district
            , primary_addresses_filtered.primary_city
            , primary_addresses_filtered.primary_state
            , primary_addresses_filtered.primary_country
        from customers
        left join primary_addresses_filtered
            on customers.customer_id = primary_addresses_filtered.customer_id
    )

    select *
    from joined