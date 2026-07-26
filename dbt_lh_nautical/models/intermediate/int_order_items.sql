with
    order_items as (
        select *
        from {{ ref('stg_order_items') }}
    )

    , enriched_order_items as (
        select
            order_item_id
            , order_id
            , product_variant_id
            , order_item_quantity
            , order_item_unit_price
            , order_item_line_total
            , order_item_icms_rate
            , order_item_ipi_rate

            -- Nominal tax calculation adjusted for decimal(14,2) precision
            , cast(order_item_line_total * (order_item_icms_rate / 100) as decimal(14, 2)) as order_item_icms_amount
            , cast(order_item_line_total * (order_item_ipi_rate / 100) as decimal(14, 2)) as order_item_ipi_amount

            -- Net revenue: what actually stays with the business after taxes are deducted
            , cast(order_item_line_total - order_item_icms_amount - order_item_ipi_amount as decimal(14, 2)) as order_item_net_revenue

            -- Internal ingestion metadata, not carried past this layer
            -- , _hash
            -- , _ingested_at
            -- , _action
        from order_items
    )

select *
from enriched_order_items
