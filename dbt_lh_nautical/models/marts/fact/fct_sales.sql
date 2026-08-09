-- Grain: one row per order item.
--
-- Business purpose:
-- Enriches sold order items with their parent order attributes, fiscal sales
-- classification, allocated order discounts, and completed return information.
-- This model supports revenue analysis by product, customer, location, channel,
-- and other item-level dimensions without double-counting order-level amounts.
--
-- Important:
-- order_total_amount is an order-level value repeated across all items from the
-- same order. It must not be summed directly at this model's grain.

with
    order_items as (
        select *
        from {{ ref('int_order_items') }}
    )

    , orders as (
        select *
        from {{ ref('int_orders') }}
    )

    -- One row per order item containing only completed and effective returns.
    -- Because this source has the same grain as fct_sales, the join below does
    -- not multiply sales rows.
    , completed_returns as (
        select *
        from {{ ref('int_completed_returns_by_order_item') }}
    )

    , sales_with_returns as (
        select
            -- Primary and foreign keys
            order_items.order_item_id
            , order_items.order_id
            , order_items.product_variant_id
            , orders.customer_id
            , orders.salesperson_id
            , orders.location_id
            , orders.fiscal_invoice_id

            -- Parent-order analytical attributes
            , orders.order_number
            , orders.order_channel
            , orders.order_status
            , orders.fiscal_invoice_status
            , orders.placed_at

            -- Centralized business classifications defined in int_orders
            , orders.is_effective_sale
            , orders.is_paid_sale_pending_invoice_fix

            -- Order-level amount repeated at item grain.
            -- Aggregate once per order_id before using it in financial KPIs.
            , orders.order_total_amount

            -- Item-level sales measures
            , order_items.order_item_quantity
            , order_items.order_item_unit_price
            , order_items.order_item_line_total
            , order_items.order_item_icms_rate
            , order_items.order_item_ipi_rate
            , order_items.order_item_icms_amount
            , order_items.order_item_ipi_amount
            , order_items.order_item_net_revenue

            -- Order-level fields required only for proportional discount
            -- allocation. They are intentionally not exposed by the final model.
            , orders.order_subtotal_amount
            , orders.order_discount_amount

            -- Completed-return measures default to zero when no effective return
            -- exists for the order item.
            , coalesce(
                completed_returns.completed_returned_quantity
                , 0
            ) as completed_returned_quantity

            , coalesce(
                completed_returns.completed_refund_amount
                , 0
            ) as completed_refund_amount

            , coalesce(
                completed_returns.has_refund
                , false
            ) as has_refund

            , coalesce(
                completed_returns.has_exchange
                , false
            ) as has_exchange

        from order_items

        left join orders
            on order_items.order_id = orders.order_id

        left join completed_returns
            on order_items.order_item_id = completed_returns.order_item_id
    )

    , calculated_sales as (
        select
            *

            -- Allocates the parent order discount proportionally according to
            -- each item's share of the order subtotal.
            --
            -- nullif prevents division by zero. The upstream data-quality test
            -- on order_subtotal_amount is responsible for surfacing invalid
            -- zero-subtotal orders.
            --
            -- Deliberately not cast/rounded here: this value is summed across
            -- every item of an order in
            -- assert_order_item_discount_allocation_reconciles, which requires
            -- full precision per item to reconcile with the order's discount
            -- amount. Rounding at this row level was tried and broke that
            -- test. If display precision needs to be controlled, do it in the
            -- BI tool's number formatting, not here.
            , order_item_net_revenue - (
                order_discount_amount
                * order_item_line_total
                / nullif(order_subtotal_amount, 0)
            ) as order_item_net_revenue_after_discount

        from sales_with_returns
    )

    , final as (
        select
            -- Keys
            {{ dbt_utils.generate_surrogate_key(['order_item_id']) }} as order_item_sk
            , order_item_id
            , order_id
            , product_variant_id
            , customer_id
            , salesperson_id
            , location_id
            , fiscal_invoice_id

            -- Order attributes and sales classifications
            , order_number
            , order_channel
            , order_status
            , fiscal_invoice_status
            , placed_at
            , is_effective_sale
            , is_paid_sale_pending_invoice_fix

            -- Order-level measure
            , order_total_amount

            -- Item-level sales measures
            , order_item_quantity
            , order_item_unit_price
            , order_item_line_total
            , order_item_icms_rate
            , order_item_ipi_rate
            , order_item_icms_amount
            , order_item_ipi_amount
            , order_item_net_revenue
            , order_item_net_revenue_after_discount

            -- Effective completed-return measures
            , completed_returned_quantity
            , completed_refund_amount
            , has_refund
            , has_exchange

            -- Item-level revenue remaining after allocated discounts and
            -- completed monetary refunds.
            , (
                order_item_net_revenue_after_discount
                - completed_refund_amount
            ) as order_item_net_revenue_after_returns

        from calculated_sales
    )

select *
from final
