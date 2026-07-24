with
    product_suppliers as (
        select *
        from {{ ref('stg_product_suppliers') }}
    )

    , selected_product_suppliers as (
        select
            {{ dbt_utils.generate_surrogate_key([
                'product_variant_id',
                'supplier_id'
            ]) }} as product_supplier_sk

            , product_variant_id
            , supplier_id
            , product_suppliers_supplier_sku as supplier_sku
            , product_suppliers_last_quoted_cost as last_quoted_cost
            , product_suppliers_lead_time_days as lead_time_days
            , created_at
            , updated_at

        from product_suppliers
    )

select *
from selected_product_suppliers
