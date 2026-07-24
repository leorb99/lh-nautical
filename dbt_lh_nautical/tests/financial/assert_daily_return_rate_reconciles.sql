-- Fails when the published daily return rate does not reconcile with its
-- exposed returned-quantity numerator and sold-quantity denominator.

select *
from {{ ref('fct_daily_financial_metrics') }}
where
    (
        sold_item_quantity = 0
        and abs(return_quantity_rate) > 0.000001
    )
    or
    (
        sold_item_quantity > 0
        and abs(
            return_quantity_rate
            - (
                returned_item_quantity
                / sold_item_quantity
            )
        ) > 0.000001
    )
