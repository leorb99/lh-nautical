with
    int_variants as (
        select *
        from {{ ref('int_product_variants') }}
    )

    , get_one_product_variant_per_row as (
        select
            product_variant_id
            , product_id
            , product_variant_sku
            , product_variant_barcode_ean
            , product_name
            , product_description
            , category_name
            , category_url_slug
            , parent_category_name
            , brand_name
            , product_ncm_code
            , product_unit_of_measure
            , product_variant_weight_kg
            , product_variant_sale_price
            , product_variant_cost_price
            , product_variant_icms_rate
            , product_variant_ipi_rate
            , attr_color
            , attr_size
            , attr_length_m
            , attr_power_hp
            , attr_voltage_v
            , attr_weight_kg
            , attr_waterproof
            , attr_material
            , is_variant_active
        from int_variants
    )

    , add_surrogate_key as (
        select
            {{ dbt_utils.generate_surrogate_key(['product_variant_id', 'product_id']) }} as product_sk
            , *
        from get_one_product_variant_per_row
    )

select *
from add_surrogate_key
