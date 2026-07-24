with
    stock_movements as (
        select *
        from {{ ref('stg_stock_movements') }}
    )

    , selected_stock_movements as (
        select
            {{ dbt_utils.generate_surrogate_key(['stock_movement_id']) }} as stock_movement_sk
            , stock_movement_id
            , product_variant_id
            , location_id
            , employee_id
            , stock_movement_type
            , stock_movement_quantity as movement_quantity_signed
            , abs(stock_movement_quantity) as movement_quantity_absolute

            , case
                when stock_movement_quantity > 0 then 'in'
                when stock_movement_quantity < 0 then 'out'
                else 'zero'
            end as movement_direction

            , case
                when stock_movement_type = 'purchase_in' then 'purchase'
                when stock_movement_type = 'sale_out' then 'sale'
                when stock_movement_type = 'return_in' then 'customer_return'
                when stock_movement_type = 'return_out' then 'supplier_return'
                else 'other'
            end as movement_business_category

            , stock_movement_reference_table
            , stock_movement_reference_id
            , stock_movement_notes
            , occurred_at
            , cast(occurred_at as date) as movement_date
            , created_at
        from stock_movements
    )

select *
from selected_stock_movements
