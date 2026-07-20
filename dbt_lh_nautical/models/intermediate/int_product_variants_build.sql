with
    product_variants as (
        select * from {{ ref('stg_product_variants') }}
    )

    , products_enriched as (
        select * from {{ ref('int_products_enriched') }}
    )

    , variant_attributes as (
        select * from {{ ref('int_variant_attributes_pivoted') }}
    )

    , final_build as (
        select
            product_variants.product_variant_id
            , products_enriched.product_id
            , product_variants.product_variant_sku
            , product_variants.product_variant_sale_price
            , product_variants.product_variant_cost_price
            , product_variants.product_variant_icms_rate
            , product_variants.product_variant_ipi_rate
            , product_variants.is_active as is_variant_active
            , products_enriched.product_name
            , products_enriched.product_description
            , products_enriched.product_unit_of_measure
            , products_enriched.is_product_active
            , products_enriched.brand_name
            , products_enriched.category_name
            , products_enriched.parent_category_name
            , variant_attributes.attr_color
            , variant_attributes.attr_size
            , variant_attributes.attr_length_m
            , variant_attributes.attr_power_hp
            , variant_attributes.attr_voltage_v
            , variant_attributes.attr_weight_kg
            , variant_attributes.attr_waterproof
            , variant_attributes.attr_material

        from product_variants

        left join products_enriched
            on product_variants.product_id = products_enriched.product_id

        left join variant_attributes
            on product_variants.product_variant_id = variant_attributes.product_variant_id
    )

select *
from final_build
