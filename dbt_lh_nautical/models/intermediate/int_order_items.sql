with
    order_items as (
        select *
        from {{ ref('stg_order_items') }}
    )

    , cleaned_order_items as (
        select
            order_item_id
            , order_id
            , product_variant_id
            , order_item_quantity
            , order_item_unit_price
        
            -- Calculate gross revenue maintaining decimal precision
            , cast(order_item_quantity * order_item_unit_price as decimal(14,2)) as order_item_gross_amount

            -- Original tax rates
            , order_item_icms_rate
            , order_item_ipi_rate
        
            -- Nominal tax calculation adjusted for decimal(14,2) precision
            , cast((order_item_quantity * order_item_unit_price) * (order_item_icms_rate / 100.0) as decimal(14,2)) as order_item_icms_amount
            , cast((order_item_quantity * order_item_unit_price) * (order_item_ipi_rate / 100.0) as decimal(14,2)) as order_item_ipi_amount
        
            , order_item_line_total
        from order_items
    )

select * from cleaned_order_items