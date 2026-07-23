with
    stock_movements as (
        select *
        from {{ ref('int_stock_movements') }}
    )

select *
from stock_movements
