-- Fails when the published daily average ticket does not reconcile with its
-- exposed revenue numerator and effective-order denominator.

select *
from {{ ref('fct_daily_financial_metrics') }}
where
    (
        effective_order_count = 0
        and abs(average_ticket_amount) > 0.01
    )
    or
    (
        effective_order_count > 0
        and abs(
            average_ticket_amount
            - (
                effective_order_revenue_amount
                / effective_order_count
            )
        ) > 0.01
    )
