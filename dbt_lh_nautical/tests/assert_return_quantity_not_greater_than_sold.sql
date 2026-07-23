select
    r.return_item_id,
    r.return_id,
    r.order_item_id,
    r.return_item_quantity,
    oi.order_item_quantity

from {{ ref('int_returns') }} as r

inner join {{ ref('int_order_items') }} as oi
    on r.order_item_id = oi.order_item_id

where r.return_item_quantity > oi.order_item_quantity
