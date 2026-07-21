with
    returns as (
        select *
        from {{ ref('int_returns') }}
    )

    , selected_returns as (
        select
            {{ dbt_utils.generate_surrogate_key(['return_item_id']) }} as return_item_sk
            , return_item_id
            , return_id
            , return_number
            , order_id
            , order_item_id
            , product_variant_id
            , customer_id
            , received_at_location_id
            , return_status
            , return_reason
            , return_item_action
            , exchange_variant_id
            , return_item_quantity
            , return_item_unit_refund_amount
            , return_item_total_refund_amount
            , return_total_refund_amount
            , returned_at
            , is_effective_return
            , is_exchange
            , is_refund
        from returns
    )

select *
from selected_returns
