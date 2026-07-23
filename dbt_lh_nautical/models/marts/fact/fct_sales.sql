with
    order_items as (
        select *
        from {{ ref('int_order_items') }}
    )

    , orders as (
        select *
        from {{ ref('int_orders') }}
    )

    , joined as (
        select
            -- keys
            order_items.order_item_id
            , order_items.order_id
            , order_items.product_variant_id
            , orders.customer_id
            , orders.salesperson_id
            , orders.location_id
            , orders.fiscal_invoice_id

            -- degenerate dimensions, denormalized (safe to repeat per item: categorical/reference)
            , orders.order_number
            , orders.order_channel
            , orders.order_status
            , orders.fiscal_invoice_status
            , orders.placed_at
            , orders.is_effective_sale

            -- Official order-level revenue field, repeated per item: do not SUM directly
            -- across items of the same order, it would double-count. Aggregate by
            -- order_id first (e.g. count(distinct order_id) for average ticket).
            , orders.order_total_amount

            -- Other order-level monetary totals, excluded: not additive at item grain, would be double-counted if summed across an order's items
            -- , orders.order_subtotal_amount
            -- , orders.order_discount_amount
            -- , orders.fiscal_invoice_total_amount
            -- , orders.authorized_fiscal_invoice_amount

            -- Order/invoice audit timestamps, excluded: no analytical value here, placed_at already covers the "when did this happen" need
            -- , orders.created_at
            -- , orders.updated_at
            -- , orders.fiscal_invoice_issued_at
            -- , orders.fiscal_invoice_created_at
            -- , orders.fiscal_invoice_updated_at

            -- Fiscal invoice administrative/compliance fields, excluded: not needed for sales analysis at this grain
            -- , orders.fiscal_invoice_nfe_number
            -- , orders.fiscal_invoice_nfe_access_key
            -- , orders.fiscal_invoice_series
            -- , orders.fiscal_invoice_xml_storage_uri

            -- measures (not order-level monetary totals, which would be double-counted if summed here)
            , order_items.order_item_quantity
            , order_items.order_item_unit_price
            , order_items.order_item_line_total
            , order_items.order_item_icms_rate
            , order_items.order_item_ipi_rate
            , order_items.order_item_icms_amount
            , order_items.order_item_ipi_amount
            , order_items.order_item_net_revenue

        from order_items
        left join orders
            on order_items.order_id = orders.order_id
    )

select *
from joined
