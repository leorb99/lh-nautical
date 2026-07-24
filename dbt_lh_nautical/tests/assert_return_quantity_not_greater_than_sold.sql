-- Asserts that the cumulative quantity effectively returned for an order item
-- does not exceed the quantity originally sold. This guards against duplicated
-- or inconsistent return records inflating downstream return metrics.
select
    completed_returns.order_item_id
    , completed_returns.completed_returned_quantity
    , order_items.order_item_quantity
from {{ ref('int_completed_returns_by_order_item') }} as completed_returns
join {{ ref('int_order_items') }} as order_items
    on completed_returns.order_item_id = order_items.order_item_id
where completed_returns.completed_returned_quantity > order_items.order_item_quantity
