with
    stock_level_snapshots as (
        select *
        from {{ ref('snp_stock_levels') }}
    )

    , standardized_inventory_snapshots as (
        select
            dbt_scd_id as inventory_snapshot_sk

            , product_variant_id
            , location_id
            , stock_level_quantity_on_hand as quantity_on_hand
            , stock_level_reorder_point as reorder_point
            , updated_at as source_updated_at

            , dbt_valid_from as snapshot_valid_from
            , dbt_valid_to as snapshot_valid_to
            , cast(dbt_valid_from as date) as snapshot_date

            , case
                when dbt_valid_to is null then true
                else false
            end as is_current_snapshot

            , case
                when stock_level_quantity_on_hand = 0 then true
                else false
            end as is_out_of_stock

            , case
                when
                    stock_level_reorder_point is not null
                    and stock_level_quantity_on_hand
                    <= stock_level_reorder_point
                    then true
                else false
            end as is_below_reorder_point

        from stock_level_snapshots
    )

select *
from standardized_inventory_snapshots
