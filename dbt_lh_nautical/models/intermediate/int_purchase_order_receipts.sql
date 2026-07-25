with
    purchase_orders as (
        select *
        from {{ ref('int_purchase_orders') }}
    )

    , purchase_order_items as (
        select *
        from {{ ref('int_purchase_order_items') }}
    )

    , goods_receipts as (
        select *
        from {{ ref('int_goods_receipts') }}
    )

    , goods_receipt_items as (
        select *
        from {{ ref('stg_goods_receipt_items') }}
    )

    , receipt_events as (
        select
            goods_receipt_items.goods_receipt_item_id
            , goods_receipts.goods_receipt_id
            , goods_receipt_items.purchase_order_item_id
            , goods_receipts.received_at
            , goods_receipts.is_future_receipt_date
            , goods_receipt_items.goods_receipt_item_quantity_received

        from goods_receipt_items

        inner join goods_receipts
            on
                goods_receipt_items.goods_receipt_id
                = goods_receipts.goods_receipt_id
    )

    , receipts_by_purchase_order_item as (
        select
            purchase_order_item_id
            , min(received_at) as first_received_at
            , max(received_at) as last_received_at

            , sum(goods_receipt_item_quantity_received)
                as total_quantity_received

            , count(distinct goods_receipt_id)
                as receipt_event_count

            , max(
                case
                    when is_future_receipt_date then 1
                    else 0
                end
            ) = 1 as has_future_receipt_date

        from receipt_events

        group by
            purchase_order_item_id
    )

    , purchase_order_receipts as (
        select
            purchase_order_items.purchase_order_item_id
            , purchase_orders.purchase_order_id
            , purchase_orders.supplier_id
            , purchase_orders.destination_location_id as location_id
            , purchase_order_items.product_variant_id
            , purchase_orders.purchase_order_status
            , purchase_orders.placed_at

            , receipts_by_purchase_order_item.first_received_at
            , receipts_by_purchase_order_item.last_received_at
            , receipts_by_purchase_order_item.receipt_event_count

            , purchase_order_items.purchase_order_item_quantity_ordered
            , coalesce(
                receipts_by_purchase_order_item.total_quantity_received
                , 0
            ) as total_quantity_received

            , cast(
                coalesce(
                    receipts_by_purchase_order_item.total_quantity_received
                    , 0
                )
                / nullif(
                    purchase_order_items.purchase_order_item_quantity_ordered
                    , 0
                )
                as decimal(14, 4)
            ) as receipt_fill_rate

            , case
                when
                    purchase_orders.placed_at is not null
                    and receipts_by_purchase_order_item.first_received_at is not null
                    and receipts_by_purchase_order_item.first_received_at
                    >= purchase_orders.placed_at
                    then datediff(
                        day
                        , purchase_orders.placed_at
                        , receipts_by_purchase_order_item.first_received_at
                    )
            end as lead_time_days

            , coalesce(
                receipts_by_purchase_order_item.has_future_receipt_date
                , false
            ) as has_future_receipt_date

        from purchase_order_items

        inner join purchase_orders
            on
                purchase_order_items.purchase_order_id
                = purchase_orders.purchase_order_id

        left join receipts_by_purchase_order_item
            on
                purchase_order_items.purchase_order_item_id
                = receipts_by_purchase_order_item.purchase_order_item_id
    )

select *
from purchase_order_receipts
