with
    purchase_order_receipts as (
        select *
        from {{ ref('int_purchase_order_receipts') }}
    )

    , open_purchase_order_items as (
        select
            product_variant_id
            , location_id
            , purchase_order_item_id
            , purchase_order_status
            , purchase_order_item_quantity_ordered
            , total_quantity_received
            , greatest(
                purchase_order_item_quantity_ordered
                - total_quantity_received
                , 0
            ) as open_purchase_order_quantity
        from purchase_order_receipts
        where purchase_order_status in ('sent', 'partially_received')
    )

    , aggregated_open_quantities as (
        select
            product_variant_id
            , location_id
            , sum(open_purchase_order_quantity) as open_purchase_order_quantity
            , count(distinct purchase_order_item_id) as open_purchase_order_item_count
        from open_purchase_order_items
        group by
            product_variant_id
            , location_id
    )

select
    product_variant_id
    , location_id
    , open_purchase_order_quantity
    , open_purchase_order_item_count
from aggregated_open_quantities
