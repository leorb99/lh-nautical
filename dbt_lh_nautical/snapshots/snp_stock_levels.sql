{% snapshot snp_stock_levels %}

{{
    config(
        target_schema='silver',
        unique_key="concat(
            cast(product_variant_id as string),
            '-',
            cast(location_id as string)
        )",
        strategy='timestamp',
        updated_at='updated_at',
        invalidate_hard_deletes=true
    )
}}

select
    product_variant_id
    , location_id
    , stock_level_quantity_on_hand
    , stock_level_reorder_point
    , updated_at
    , _hash
    , _ingested_at
    , _action
from {{ ref('stg_stock_levels') }}

{% endsnapshot %}
