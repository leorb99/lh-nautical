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
            v.product_variant_id as product_variant_id
            , p.product_id
            , v.product_variant_sku
            , v.product_variant_sale_price
            , v.product_variant_cost_price
            , v.product_variant_icms_rate
            , v.product_variant_ipi_rate
            , v.is_active as is_variant_active
            , p.product_name
            , p.product_description
            , p.product_unit_of_measure
            , p.is_product_active
            , p.brand_name
            , p.category_name
            , p.parent_category_name
            , a.attr_color
            , a.attr_size
            , a.attr_length_m
            , a.attr_power_hp
            , a.attr_voltage_v
            , a.attr_weight_kg
            , a.attr_waterproof
            , a.attr_material
            
        from product_variants v
        
        left join products_enriched p 
            on v.product_id = p.product_id
            
        left join variant_attributes a 
            on v.product_variant_id = a.product_variant_id
    )

select *
from final_build