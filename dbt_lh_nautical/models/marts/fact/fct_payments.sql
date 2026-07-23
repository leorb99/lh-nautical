with
    payments as (
        select *
        from {{ ref('stg_payments') }}
    )

    , dim_payment_method as (
        select *
        from {{ ref('dim_payment_methods') }}
    )

    , join_dim_payments as (
        select
            payments.payment_id
            , payments.order_id
            , dim_payment_method.payment_method_sk
            , payments.payment_installments
            , payments.payment_amount
            , payments.payment_status
            , case when payments.created_at > current_timestamp() then null else payments.created_at end as payment_created_at
        from payments
        left join dim_payment_method
            on payments.payment_method = dim_payment_method.payment_method_name
    )

    , add_surrogate_key as (
        select
            {{ dbt_utils.generate_surrogate_key(['payment_id']) }} as payment_sk
            , *
        from join_dim_payments
    )

select *
from add_surrogate_key
