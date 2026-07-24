with
    stock_movements as (
        select *
        from {{ ref('int_stock_movements') }}

        -- Future-dated movements are excluded so they cannot shift the
        -- analysis date or affect any downstream demand windows.
        where movement_business_category = 'sale'
            and occurred_at <= current_timestamp()
    )

    -- Uses the most recent valid sale date as the common reference date
    -- for all product variants and inventory locations.
    , reference_date as (
        select
            max(movement_date) as inventory_analysis_date
        from stock_movements
    )

    , demand_metrics as (
        select
            stock_movements.product_variant_id
            , stock_movements.location_id
            , reference_date.inventory_analysis_date

            , sum(
                case
                    when stock_movements.movement_date
                        between date_sub(
                            reference_date.inventory_analysis_date,
                            29
                        )
                        and reference_date.inventory_analysis_date
                    then stock_movements.movement_quantity_absolute
                    else 0
                end
            ) as sales_quantity_last_30_days

            , sum(
                case
                    when stock_movements.movement_date
                        between date_sub(
                            reference_date.inventory_analysis_date,
                            59
                        )
                        and reference_date.inventory_analysis_date
                    then stock_movements.movement_quantity_absolute
                    else 0
                end
            ) as sales_quantity_last_60_days

            , sum(
                case
                    when stock_movements.movement_date
                        between date_sub(
                            reference_date.inventory_analysis_date,
                            89
                        )
                        and reference_date.inventory_analysis_date
                    then stock_movements.movement_quantity_absolute
                    else 0
                end
            ) as sales_quantity_last_90_days

            , count(
                distinct case
                    when stock_movements.movement_date
                        between date_sub(
                            reference_date.inventory_analysis_date,
                            29
                        )
                        and reference_date.inventory_analysis_date
                    then stock_movements.movement_date
                end
            ) as days_with_sales_last_30_days

            , count(
                distinct case
                    when stock_movements.movement_date
                        between date_sub(
                            reference_date.inventory_analysis_date,
                            59
                        )
                        and reference_date.inventory_analysis_date
                    then stock_movements.movement_date
                end
            ) as days_with_sales_last_60_days

            , count(
                distinct case
                    when stock_movements.movement_date
                        between date_sub(
                            reference_date.inventory_analysis_date,
                            89
                        )
                        and reference_date.inventory_analysis_date
                    then stock_movements.movement_date
                end
            ) as days_with_sales_last_90_days

            , max(stock_movements.movement_date) as last_sale_date

        from stock_movements
        cross join reference_date

        where stock_movements.movement_date
            between date_sub(
                reference_date.inventory_analysis_date,
                89
            )
            and reference_date.inventory_analysis_date

        group by
            stock_movements.product_variant_id
            , stock_movements.location_id
            , reference_date.inventory_analysis_date
    )

    , calculated_metrics as (
        select
            product_variant_id
            , location_id
            , inventory_analysis_date

            , sales_quantity_last_30_days
            , sales_quantity_last_60_days
            , sales_quantity_last_90_days

            , days_with_sales_last_30_days
            , days_with_sales_last_60_days
            , days_with_sales_last_90_days

            , last_sale_date

            -- Calendar-day averages include days without sales by dividing
            -- cumulative demand by the full 30, 60 and 90-day windows.
            , cast(
                sales_quantity_last_30_days / 30.0
                as decimal(14, 3)
            ) as average_daily_sales_30d

            , cast(
                sales_quantity_last_60_days / 60.0
                as decimal(14, 3)
            ) as average_daily_sales_60d

            , cast(
                sales_quantity_last_90_days / 90.0
                as decimal(14, 3)
            ) as average_daily_sales_90d

        from demand_metrics
    )

select *
from calculated_metrics
