select
    r.order_item_id,
    r.completed_refund_amount,
    oi.order_item_line_total

from {{ ref('int_completed_returns_by_order_item') }} as r

inner join {{ ref('int_order_items') }} as oi
    on r.order_item_id = oi.order_item_id

where r.completed_refund_amount > oi.order_item_line_total
