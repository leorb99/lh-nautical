-- Asserts that the cumulative effective refund amount for an order item does
-- not exceed its original line total. This guards against duplicated or
-- inconsistent refund records overstating downstream financial deductions.
select
    completed_returns.order_item_id
    , completed_returns.completed_refund_amount
    , order_items.order_item_line_total
from {{ ref('int_completed_returns_by_order_item') }} as completed_returns
join {{ ref('int_order_items') }} as order_items
    on completed_returns.order_item_id = order_items.order_item_id
where completed_returns.completed_refund_amount > order_items.order_item_line_total
