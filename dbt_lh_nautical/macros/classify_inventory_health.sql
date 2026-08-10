{% macro classify_inventory_health(quantity_on_hand, average_daily_sales_90d) %}

    -- Shared between fct_current_inventory and fct_inventory_replenishment,
    -- both of which have quantity_on_hand and average_daily_sales_90d at the
    -- same product_variant_id + location_id grain. Centralizing this here
    -- means the 7/15-day thresholds only need to change in one place.
    case
        when {{ quantity_on_hand }} = 0
            then 'out_of_stock'

        when coalesce({{ average_daily_sales_90d }}, 0) = 0
            then 'no_recent_sales'

        when ({{ quantity_on_hand }} / {{ average_daily_sales_90d }}) <= 7
            then 'critical'

        when ({{ quantity_on_hand }} / {{ average_daily_sales_90d }}) <= 15
            then 'attention'

        else 'healthy'
    end

{% endmacro %}
