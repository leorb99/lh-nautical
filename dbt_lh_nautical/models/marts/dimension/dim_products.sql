with
    product_variants as (
        select *
        from {{ ref('stg_product_variants') }}
    )

    , enriched_products as (
        select *
        from {{ ref('int_products_enriched') }}
    )

    , variant_attributes as (
        select *
        from {{ ref('int_variant_attributes') }}
    )

    , get_one_product_variant_per_row as (
        select
            product_variants.product_variant_id
            , enriched_products.product_id
            , product_variants.product_variant_sku
            , product_variants.product_variant_barcode_ean
            , enriched_products.product_name
            , enriched_products.product_description
            , enriched_products.unit_of_measure
            , enriched_products.product_ncm_code
            , enriched_products.category_name
            , enriched_products.category_url_slug
            , enriched_products.parent_category_name
            , enriched_products.brand_name
            , enriched_products.brand_country
            , product_variants.product_variant_weight_kg
            , product_variants.product_variant_sale_price
            , product_variants.product_variant_cost_price
            , product_variants.product_variant_icms_rate
            , product_variants.product_variant_ipi_rate
            , variant_attributes.attr_color
            , variant_attributes.attr_size
            , variant_attributes.attr_length_m
            , variant_attributes.attr_power_hp
            , variant_attributes.attr_voltage_v
            , variant_attributes.attr_weight_kg
            , variant_attributes.attr_waterproof
            , variant_attributes.attr_material
            , variant_attributes.is_variant_active
            , enriched_products.is_product_active
            , enriched_products.is_brand_active
            , enriched_products.is_category_active
            , enriched_products.product_created_at
            , enriched_products.brand_created_at
        from product_variants
        left join enriched_products
            on product_variants.product_id = enriched_products.product_id
        left join variant_attributes
            on product_variants.product_variant_id = variant_attributes.product_variant_id
    )

    , add_surrogate_key as (
        select
            {{ dbt_utils.generate_surrogate_key(['product_variant_id', 'product_id']) }} as product_sk
            , *
        from get_one_product_variant_per_row
    )

select *
from add_surrogate_key
