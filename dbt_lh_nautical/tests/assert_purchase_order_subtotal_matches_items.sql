-- Asserts that each purchase order's subtotal reconciles with the sum of its
-- line items' totals. These two values come from independent parts of the
-- source system and are not enforced to match by any SQL constraint, so this
-- guards against future divergence
select
    purchase_orders.purchase_order_id
    , purchase_orders.purchase_order_subtotal
    , sum(purchase_order_items.purchase_order_item_line_total) as calculated_subtotal
from {{ ref('int_purchase_orders') }} as purchase_orders
join {{ ref('int_purchase_order_items') }} as purchase_order_items
    on purchase_orders.purchase_order_id = purchase_order_items.purchase_order_id
group by purchase_orders.purchase_order_id, purchase_orders.purchase_order_subtotal
having round(purchase_orders.purchase_order_subtotal, 2) != round(sum(purchase_order_items.purchase_order_item_line_total), 2)
