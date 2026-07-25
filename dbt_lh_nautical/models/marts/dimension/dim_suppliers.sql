with
    suppliers as (
        select *
        from {{ ref('stg_suppliers') }}
    )

    , selected_suppliers as (
        select
            {{ dbt_utils.generate_surrogate_key(['supplier_id']) }}
                as supplier_sk

            , supplier_id
            , supplier_legal_name
            , supplier_trade_name

            , coalesce(
                supplier_trade_name
                , supplier_legal_name
            ) as supplier_display_name

            , supplier_country
            , supplier_tax_id
            , supplier_tax_id_type
            , supplier_email
            , supplier_phone
            , supplier_contact_name
            , is_active

            , case
                when is_active then 'active'
                else 'inactive'
            end as supplier_status

            , created_at
            , updated_at
        from suppliers
    )

select *
from selected_suppliers
