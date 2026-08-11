-- Grain: one row per calendar date.
--
-- Business purpose:
-- Provides auditable daily financial KPIs derived from fct_sales, including
-- effective sales revenue, item-level net revenue after completed refunds,
-- average ticket, quantity-based return rate, and revenue pending fiscal
-- regularization.
--
-- Date attribution rule:
-- Sales and returns are attributed to the original order placed_at date.
-- Therefore, completed refunds reduce the financial result of the original
-- sales cohort rather than the date on which the return was processed.
--
-- Aggregation guidance:
-- Additive measures and their numerators/denominators are exposed together.
-- For periods longer than one day, average_ticket_amount and
-- return_quantity_rate must be recalculated from their underlying totals,
-- rather than averaged across daily rows.

with
    sales as (
        select *
        from {{ ref('fct_sales') }}
    )

    -- Effective sales are paid orders with an authorized fiscal invoice and a
    -- valid order date, according to the centralized rule in int_orders.
    , effective_sales as (
        select *
        from sales
        where is_effective_sale = true
    )

    -- Item-grain additive measures for effective sales.
    , daily_effective_item_metrics as (
        select
            cast(placed_at as date) as metric_date
            , sum(order_item_quantity) as sold_item_quantity
            , sum(completed_returned_quantity) as returned_item_quantity
            , sum(completed_refund_amount) as completed_refund_amount

            -- Cast to decimal(14,2): same unbounded-precision issue as
            -- average_ticket_amount/return_quantity_rate below, caught via
            -- information_schema rather than by a test.
            , cast(
                sum(order_item_net_revenue_after_returns)
                as decimal(14, 2)
            ) as net_revenue_amount
        from effective_sales
        group by cast(placed_at as date)
    )

    -- order_total_amount is repeated at the order-item grain in fct_sales.
    -- Reducing to one row per order prevents double counting.
    , effective_orders as (
        select
            cast(placed_at as date) as metric_date
            , order_id
            , max(order_total_amount) as order_total_amount
        from effective_sales
        group by
            cast(placed_at as date)
            , order_id
    )

    , daily_effective_order_metrics as (
        select
            metric_date
            , count(*) as effective_order_count
            , sum(order_total_amount) as effective_order_revenue_amount
        from effective_orders
        group by metric_date
    )

    -- Paid orders with rejected fiscal invoices are reported separately.
    -- They are not included in effective sales or official financial KPIs.
    , pending_invoice_orders as (
        select
            cast(placed_at as date) as metric_date
            , order_id
            , max(order_total_amount) as order_total_amount
        from sales
        where is_paid_sale_pending_invoice_fix = true
        group by
            cast(placed_at as date)
            , order_id
    )

    , daily_pending_invoice_metrics as (
        select
            metric_date
            , count(*) as pending_invoice_order_count
            , sum(order_total_amount) as pending_invoice_revenue_amount
        from pending_invoice_orders
        group by metric_date
    )

    -- Includes dates that contain either effective sales or revenue pending
    -- fiscal regularization.
    , metric_dates as (
        select metric_date
        from daily_effective_item_metrics

        union distinct

        select metric_date
        from daily_pending_invoice_metrics
    )

    , combined_metrics as (
        select
            metric_dates.metric_date

            , coalesce(
                daily_effective_order_metrics.effective_order_count
                , 0
            ) as effective_order_count

            , coalesce(
                daily_effective_order_metrics.effective_order_revenue_amount
                , 0
            ) as effective_order_revenue_amount

            , coalesce(
                daily_effective_item_metrics.net_revenue_amount
                , 0
            ) as net_revenue_amount

            , coalesce(
                daily_effective_item_metrics.completed_refund_amount
                , 0
            ) as completed_refund_amount

            , coalesce(
                daily_effective_item_metrics.sold_item_quantity
                , 0
            ) as sold_item_quantity

            , coalesce(
                daily_effective_item_metrics.returned_item_quantity
                , 0
            ) as returned_item_quantity

            , coalesce(
                daily_pending_invoice_metrics.pending_invoice_order_count
                , 0
            ) as pending_invoice_order_count

            , coalesce(
                daily_pending_invoice_metrics.pending_invoice_revenue_amount
                , 0
            ) as pending_invoice_revenue_amount

        from metric_dates

        left join daily_effective_item_metrics
            on
                metric_dates.metric_date
                = daily_effective_item_metrics.metric_date

        left join daily_effective_order_metrics
            on
                metric_dates.metric_date
                = daily_effective_order_metrics.metric_date

        left join daily_pending_invoice_metrics
            on
                metric_dates.metric_date
                = daily_pending_invoice_metrics.metric_date
    )

    , final as (
        select
            metric_date

            -- Auditable financial totals
            , effective_order_count
            , effective_order_revenue_amount
            , net_revenue_amount
            , completed_refund_amount
            , sold_item_quantity
            , returned_item_quantity
            , pending_invoice_order_count
            , pending_invoice_revenue_amount

            -- Cast to a fixed scale: division between DECIMAL columns widens
            -- precision automatically. Target scale matches the tolerance of
            -- assert_daily_average_ticket_reconciles (0.01) and
            -- assert_daily_return_rate_reconciles (0.000001) below.
            --
            -- Average official order value for effective sales.
            , cast(
                case
                    when effective_order_count > 0
                        then (
                            effective_order_revenue_amount
                            / effective_order_count
                        )
                    else 0
                end
                as decimal(14, 2)
            ) as average_ticket_amount

            -- Quantity-based return rate for effective sales.
            , cast(
                case
                    when sold_item_quantity > 0
                        then (
                            returned_item_quantity
                            / sold_item_quantity
                        )
                    else 0
                end
                as decimal(14, 6)
            ) as return_quantity_rate

        from combined_metrics
    )

select *
from final
