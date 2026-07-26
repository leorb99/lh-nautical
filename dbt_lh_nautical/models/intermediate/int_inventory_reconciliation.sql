with
    inventory_snapshots as (
        select *
        from {{ ref('int_inventory_snapshots') }}
    )

    , snapshot_periods as (
        select
            inventory_snapshot_sk
            , product_variant_id
            , location_id
            , quantity_on_hand
            , snapshot_valid_from
            , snapshot_valid_to
            , is_current_snapshot

            , lag(quantity_on_hand) over (
                partition by
                    product_variant_id
                    , location_id
                order by snapshot_valid_from
            ) as previous_quantity_on_hand

            , lag(snapshot_valid_from) over (
                partition by
                    product_variant_id
                    , location_id
                order by snapshot_valid_from
            ) as previous_snapshot_valid_from

        from inventory_snapshots
    )

    , movements_between_snapshots as (
        select
            snapshot_periods.inventory_snapshot_sk
            , sum(
                coalesce(
                    stock_movements.movement_quantity_signed
                    , 0
                )
            ) as net_movement_quantity

        from snapshot_periods

        left join {{ ref('int_stock_movements') }} as stock_movements
            on
                snapshot_periods.product_variant_id
                = stock_movements.product_variant_id
                and snapshot_periods.location_id
                = stock_movements.location_id
                and snapshot_periods.previous_snapshot_valid_from
                < stock_movements.occurred_at
                and snapshot_periods.snapshot_valid_from
                >= stock_movements.occurred_at

        where snapshot_periods.previous_snapshot_valid_from is not null

        group by snapshot_periods.inventory_snapshot_sk
    )

    , reconciled_inventory as (
        select
            snapshot_periods.inventory_snapshot_sk
            , snapshot_periods.product_variant_id
            , snapshot_periods.location_id
            , snapshot_periods.previous_snapshot_valid_from
            , snapshot_periods.snapshot_valid_from
            , snapshot_periods.previous_quantity_on_hand
            , snapshot_periods.quantity_on_hand as observed_quantity_on_hand

            , snapshot_periods.quantity_on_hand
            - snapshot_periods.previous_quantity_on_hand
                as observed_quantity_change

            , coalesce(
                movements_between_snapshots.net_movement_quantity
                , 0
            ) as net_movement_quantity

            , (
                snapshot_periods.quantity_on_hand
                - snapshot_periods.previous_quantity_on_hand
            ) - coalesce(
                movements_between_snapshots.net_movement_quantity
                , 0
            ) as reconciliation_difference

            , case
                when
                    abs(
                        (
                            snapshot_periods.quantity_on_hand
                            - snapshot_periods.previous_quantity_on_hand
                        ) - coalesce(
                            movements_between_snapshots.net_movement_quantity
                            , 0
                        )
                    ) < 0.001
                    then 'reconciled'
                else 'difference_detected'
            end as reconciliation_status

        from snapshot_periods

        left join movements_between_snapshots
            on
                snapshot_periods.inventory_snapshot_sk
                = movements_between_snapshots.inventory_snapshot_sk

        where snapshot_periods.previous_snapshot_valid_from is not null
    )

select *
from reconciled_inventory
