with
    payments as (
        select *
        from {{ ref('stg_payments') }}
    )

    , get_payment_method as (
        select distinct payment_method
        from payments
    )

    , add_surrogate_key as (
        select
            {{ dbt_utils.generate_surrogate_key(['payment_method']) }} as payment_method_sk
            , payment_method as payment_method_name
        from get_payment_method
    )

select *
from add_surrogate_key
