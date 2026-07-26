-- Assserts that each order's discount, when allocated proportionally across its
-- items (order_item_net_revenuue - order_item_net_revenue_after_discount), sums
-- back up to the order's own order_discount_amount. The allocation formula is
-- not enforced to reconcile by any SQL constraint, so this guards against
-- future divergence (e.g. a change to the allocation formula)
select
    orders.order_id
    , orders.order_discount_amount
    , sum(sales.order_item_net_revenue - sales.order_item_net_revenue_after_discount) as calculated_allocated_discount
from {{ ref('int_orders') }} as orders
inner join {{ ref('fct_sales') }} as sales
    on orders.order_id = sales.order_id
group by orders.order_id, orders.order_discount_amount
having round(orders.order_discount_amount, 2) != round(sum(sales.order_item_net_revenue - sales.order_item_net_revenue_after_discount), 2)
