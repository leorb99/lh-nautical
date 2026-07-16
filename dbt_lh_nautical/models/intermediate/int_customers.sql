with
    customers as (
        select *
        from {{ ref('stg_customers') }}
    )

    , addresses as (
        select *
        from {{ ref('stg_addresses')}}
    )

    , cleaned_customers as (
        select
            customer_id
            , customer_person_type

            -- Removes known titles (Dr., Dra. Sr., Sra., Srta.) confirmed via regexp_extract discovery 
            -- and handles placeholders like 'TBD' to null
            , case
                when lower(trim(customer_legal_name)) = 'tbd' then null
                else trim(regexp_replace(customer_legal_name, '^(Dr\\.|Dra\\.|Sr\\.|Sra\\.|Srta\\.)\\s+', ''))
            end as customer_legal_name

            , customer_trade_name
            , customer_tax_id

            -- Converts placeholders 'NAO POSSUI' and '0' to null
            -- 'ISENTO' is a valid fiscal status (tax-exempt), preserved distinctly
            , case
                when lower(trim(customer_state_registration)) in ('nao possui', '0') then null
                when lower(trim(customer_state_registration)) = 'isento' then 'TAX-EXEMPT'
                else trim(customer_state_registration)
            end as customer_state_registration

            -- Standardizes email addresses
            -- Note: numeric suffix before '@' (e.g. nome.1500@example.com) is a synthetic 
            -- data generator artifact, not a quality issue, therefore: left untouched
            , lower(trim(customer_email)) as customer_email

            -- Nullifies placeholder phone numbers (e.g. '0000000000', '1234567890', 'tel.: 0000') confirmed via discovery
            -- Valid phones follow the pattern '55' + DDD + number (12-13 digits)
            , case 
                when length(regexp_replace(customer_phone, '[^0-9]', '')) < 12 then null
            else trim(customer_phone)
            end as customer_phone
            
            , customer_is_active
            , created_at
            , updated_at
        from customers
    )       

    , cleaned_addresses as (
        select
            address_id
            , customer_id
            , address_type

            -- Standardizes postal code to contain only numbers, removing the inconsistent
            -- hyphen formatting found in the source via discovery
            , regexp_replace(address_postal_code, '[^0-9]', '') as address_postal_code

            -- Trims whitespace, confirmed via discovery to have leading/trailing spaces
            , trim(address_street_name) as address_street_name

            , address_number
            
            -- Nullifies placeholder values found via discovery: pure punctuation ('.', '-', '?', and variations)
            -- and common placeholders ('TBD', 'FIXME', 'TODO', 'test', 'asdf', 'xxx', 'N/A').
            , case 
                when trim(address_complement) rlike '^[.\\-?]+$' then null
                when lower(trim(address_complement)) in ('tbd', 'fixme', 'todo', 'test', 'asdf', 'xxx', 'n/a') then null
                else address_complement
            end as address_complement

            -- Trims whitespace, confirmed via discovery to have leading/trailing spaces
            , trim(address_district) as address_district

            , address_city
            , address_state
            , address_country
            , address_is_primary
            , _hash
            , _ingested_at
            , _action
        from addresses            
    )

    , primary_addresses_filtered as (
        select
            address_id as primary_address_id
            , customer_id 
            , address_type as primary_address_type
            , address_postal_code as primary_address_postal_code
            , address_street_name as primary_address_street_name
            , address_number as primary_address_number
            , address_complement as primary_address_complement
            , address_district as primary_address_district
            , address_city as primary_address_city
            , address_state as primary_address_state
            , address_country as primary_address_country
            -- , address_is_primary
            -- , _hash
            -- , _ingested_at
            -- , _action
        from cleaned_addresses

        -- To ensure only one record per customer, 
        -- and in case there is more than one address marked as primary by mistake,
        -- we're using row_number to get the the most recent primary address
        qualify row_number() over(
            partition by customer_id
            order by address_is_primary desc, _ingested_at desc
        ) = 1
    )

    , joined as (
        select
            -- customer attributes:
            cleaned_customers.customer_id
            , cleaned_customers.customer_person_type
            , cleaned_customers.customer_legal_name
            , cleaned_customers.customer_trade_name
            , cleaned_customers.customer_tax_id
            , cleaned_customers.customer_state_registration
            , cleaned_customers.customer_email
            , cleaned_customers.customer_phone
            , cleaned_customers.customer_is_active
            , cleaned_customers.created_at
            , cleaned_customers.updated_at

            -- primary address attributes:
            , primary_addresses_filtered.primary_address_id
            -- , primary_addresses_filtered.customer_id
            , primary_addresses_filtered.primary_address_type
            , primary_addresses_filtered.primary_address_postal_code
            , primary_addresses_filtered.primary_address_street_name
            , primary_addresses_filtered.primary_address_number
            , primary_addresses_filtered.primary_address_complement
            , primary_addresses_filtered.primary_address_district
            , primary_addresses_filtered.primary_address_city
            , primary_addresses_filtered.primary_address_state
            , primary_addresses_filtered.primary_address_country

        from cleaned_customers
        left join primary_addresses_filtered
            on cleaned_customers.customer_id = primary_addresses_filtered.customer_id
    )

    select *
    from joined