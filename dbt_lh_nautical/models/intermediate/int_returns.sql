with
    returns as (
        select *
        from {{ ref('stg_returns') }}
    )

    , return_items as (
        select *
        from {{ ref('stg_return_items') }}
    )

    , order_items as (
        select *
        from {{ ref('int_order_items') }}
    )

    , joined_returns as (
        select
            return_items.return_item_id
            , returns.return_id
            , returns.return_number
            , returns.order_id
            , return_items.order_item_id
            , order_items.product_variant_id
            , returns.customer_id
            , returns.received_at_location_id
            , returns.return_status
            , returns.return_reason
            , return_items.return_item_action
            , return_items.exchange_variant_id
            , return_items.return_item_quantity
            , return_items.return_item_unit_refund_amount

            , cast(
                return_items.return_item_quantity
                * return_items.return_item_unit_refund_amount
                as decimal(14, 2)
            ) as return_item_total_refund_amount

            , returns.return_total_refund_amount
            , returns.created_at as returned_at
            , returns.updated_at

            , case
                when returns.return_status = 'completed' then true
                else false
            end as is_effective_return

            , case
                when return_items.return_item_action = 'exchange' then true
                else false
            end as is_exchange

            , case
                when return_items.return_item_action = 'refund' then true
                else false
            end as is_refund
        from return_items
        inner join returns
            on return_items.return_id = returns.return_id
        left join order_items
            on return_items.order_item_id = order_items.order_item_id
    )

select *
from joined_returns
