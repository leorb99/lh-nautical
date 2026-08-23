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

    -- Human-readable label for BI/presentation layers, derived once from
    -- payment_method_name so the source values stay the single source of truth.
    , labeled as (
        select
            *
            , case payment_method_name
                when 'bank_transfer' then 'Transferência Bancária'
                when 'boleto' then 'Boleto'
                when 'cash' then 'Dinheiro'
                when 'credit_card' then 'Cartão de Crédito'
                when 'debit_card' then 'Cartão de Débito'
                when 'pix' then 'Pix'
                else initcap(payment_method_name)
            end as payment_method_label
        from add_surrogate_key
    )

select *
from labeled
