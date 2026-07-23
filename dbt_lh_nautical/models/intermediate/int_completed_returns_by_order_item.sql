with valid_returns as (

    select
        order_item_id,
        order_id,
        product_variant_id,
        received_at_location_id,
        return_item_quantity,
        return_item_total_refund_amount,
        returned_at,
        is_refund,
        is_exchange

    from {{ ref('int_returns') }}

    where is_effective_return = true
      and cast(returned_at as date) <= current_date()

),

aggregated as (

    select
        order_item_id,
        max(order_id) as order_id,
        max(product_variant_id) as product_variant_id,
        max(received_at_location_id) as received_at_location_id,
        sum(return_item_quantity) as completed_returned_quantity,
        sum(
            case
                when is_refund = true
                then return_item_total_refund_amount
                else 0
            end
        ) as completed_refund_amount,
        max(returned_at) as latest_returned_at,
        max(case when is_refund = true then true else false end) as has_refund,
        max(case when is_exchange = true then true else false end) as has_exchange

    from valid_returns

    group by order_item_id

)

select *
from aggregated
