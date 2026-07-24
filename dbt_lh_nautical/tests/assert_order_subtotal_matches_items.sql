-- Asserts that each order's subtotal reconciles with the sum of its line
-- items' totals. This is the assumption the discount allocation formula in
-- fct_sales relies on (order_item_line_total / order_subtotal_amount as each
-- item's share of the order). These two values come from independent parts
-- of the source system and are not enforced to match by any SQL constraint,
-- so this guards against future divergence
select
    orders.order_id
    , orders.order_subtotal_amount
    , sum(order_items.order_item_line_total) as calculated_subtotal
from {{ ref('int_orders') }} as orders
inner join {{ ref('int_order_items') }} as order_items
    on orders.order_id = order_items.order_id
group by orders.order_id, orders.order_subtotal_amount
having round(orders.order_subtotal_amount, 2) != round(sum(order_items.order_item_line_total), 2)
