with
    product_variants as (
        select *
        from {{ ref('stg_product_variants') }}
    )

    , products as (
        select *
        from {{ ref('stg_products') }}
    )

    , attributes as (
        select *
        from {{ ref('stg_attributes') }}
    )

    , variant_values as (
        select *
        from {{ ref('stg_variant_attribute_values') }}
    )

    , brands as (
        select *
        from {{ ref('stg_brands') }}
    )

    -- Self-join to flattening the category atribute
    , categories as (
        select
            child.category_id
            , child.category_name
            , child.category_url_slug
            , parent.category_id as parent_category_id
            , parent.category_name as parent_category_name

        from {{ ref('stg_categories') }} as child
        left join {{ ref('stg_categories') }} as parent
            on child.parent_category_id = parent.category_id
    )

    , valid_colors as (
        select *
        from {{ ref('valid_colors') }}
    )

    , cleaned_products as (
        select
            products.product_id
            , products.category_id
            , products.brand_id
            -- Some products lack name information, so we use a limited version of the description to name them.
            -- If the description is also null, we have a traceable placeholder.
            , coalesce(
                {{ nullify_garbage('products.product_name') }}
                , left({{ nullify_garbage('products.product_description') }}, 40)
                , 'Produto Não Identificado (ID: ' || cast(products.product_id as string) || ')'
            ) as product_name
            , {{ nullify_garbage('products.product_description') }} as product_description
            , products.product_ncm_code
            , products.product_unit_of_measure
            , products.is_active as is_product_active
        from products
    )

    , enriched_products as (
        select
            cleaned_products.product_id
            , cleaned_products.product_name
            , cleaned_products.product_description
            , cleaned_products.product_unit_of_measure
            , cleaned_products.product_ncm_code
            , cleaned_products.is_product_active
            , brands.brand_id
            , brands.brand_name
            , categories.category_id
            , categories.category_name
            , categories.category_url_slug
            , categories.parent_category_id
            , categories.parent_category_name
        from cleaned_products
        left join brands
            on cleaned_products.brand_id = brands.brand_id
        left join categories
            on cleaned_products.category_id = categories.category_id
    )

    , cleaned_raw_values as (
        select
            product_variant_id
            , attribute_id
            , {{ nullify_garbage('value') }} as raw_value
        from variant_values
    )

    , joined_attributes as (
        select
            cleaned_raw_values.product_variant_id
            , attributes.attribute_name
            , attributes.attribute_data_type
            , cleaned_raw_values.raw_value
        from cleaned_raw_values
        left join attributes on cleaned_raw_values.attribute_id = attributes.attribute_id
    )

    -- Prepares the color strings before cross join to optimize the Levenshtein distance calculation
    , normalized_colors as (
        select
            product_variant_id
            , attribute_name
            , raw_value
            , trim(lower(raw_value)) as normalized_raw_value
        from joined_attributes
        where
            attribute_name = 'color'
            and raw_value is not null
    )

    -- Cross join to calculate Levenshtein distance between normalized raw values and valid colors
    , color_distances as (
        select
            normalized_colors.product_variant_id
            , normalized_colors.attribute_name
            , normalized_colors.raw_value
            , valid_colors.valid_colors
            , levenshtein(normalized_colors.normalized_raw_value, valid_colors.valid_colors) as distance
        from normalized_colors
        cross join valid_colors
    )

    -- Agregation to find the best match for each product_variant_id and attribute_name
    , fuzzy_matched_colors as (
        select
            product_variant_id
            , attribute_name
            , raw_value
            , min_by(valid_colors, distance) as best_match_color
            , min(distance) as distance
        from color_distances
        group by 1, 2, 3
    )

    -- Combines the original data with the match fuzzy
    , combined_with_fuzzy as (
        select
            joined_attributes.product_variant_id
            , joined_attributes.attribute_name
            , joined_attributes.attribute_data_type
            , joined_attributes.raw_value
            , fuzzy_matched_colors.best_match_color
            , fuzzy_matched_colors.distance
        from joined_attributes
        left join fuzzy_matched_colors
            on
                joined_attributes.product_variant_id = fuzzy_matched_colors.product_variant_id
                and joined_attributes.attribute_name = fuzzy_matched_colors.attribute_name
    )

    , cleaned_values as (
        select
            product_variant_id
            , attribute_name
            , attribute_data_type
            , case
                when attribute_data_type = 'text'
                    then
                        case
                            when attribute_name = 'color'
                                then
                                    -- If the difference is up to 2 characters, accept the correctionormalized_colors.
                                    -- If it is greater, set it to NULL.
                                    case
                                        when distance <= 2 then best_match_color
                                    end

                            when attribute_name = 'size'
                                then
                                    case
                                        when trim(lower(raw_value)) = 'gx' then 'xg'
                                        else trim(lower(raw_value))
                                    end
                            else trim(lower(raw_value))
                        end

                when attribute_data_type = 'number'
                    then
                        cast(
                            try_cast(
                                regexp_replace(raw_value, '[^0-9.]', '')
                                as decimal(10, 2)
                            )
                            as string
                        )

                when attribute_data_type = 'boolean'
                    then
                        case
                            when trim(lower(raw_value)) in ('true', 'truue', '1', 't', 'sim', 's', 'y', 'yes') then 'true'
                            when trim(lower(raw_value)) in ('false', 'fase', '0', 'f', 'nao', 'n', 'no') then 'false'
                        end
                else raw_value
            end as attribute_value

        from combined_with_fuzzy
    )

    , pivoted as (
        select
            product_variant_id
            , max(case when attribute_name = 'color' then attribute_value end) as attr_color
            , max(case when attribute_name = 'size' then attribute_value end) as attr_size
            , max(case when attribute_name = 'length_m' then cast(attribute_value as decimal(10, 2)) end) as attr_length_m
            , max(case when attribute_name = 'power_hp' then cast(attribute_value as decimal(10, 2)) end) as attr_power_hp
            , max(case when attribute_name = 'voltage_v' then cast(attribute_value as decimal(10, 2)) end) as attr_voltage_v
            , max(case when attribute_name = 'weight_kg' then cast(attribute_value as decimal(10, 2)) end) as attr_weight_kg
            , max(case when attribute_name = 'waterproof' then cast(attribute_value as boolean) end) as attr_waterproof
            , max(case when attribute_name = 'material' then attribute_value end) as attr_material
        from cleaned_values
        group by 1
    )

    , cleaned_variant_products as (
        select
            product_variants.product_variant_id
            , enriched_products.product_id
            , product_variants.product_variant_sku
            , product_variants.product_variant_sale_price
            , product_variants.product_variant_cost_price
            , product_variants.product_variant_icms_rate
            , product_variants.product_variant_ipi_rate
            , product_variants.is_active as is_variant_active
            , product_variants.product_variant_barcode_ean
            , product_variants.product_variant_weight_kg
            , enriched_products.product_name
            , enriched_products.product_description
            , enriched_products.product_unit_of_measure
            , enriched_products.is_product_active
            , enriched_products.brand_name
            , enriched_products.category_name
            , enriched_products.category_url_slug
            , enriched_products.product_ncm_code
            , enriched_products.parent_category_name
            , pivoted.attr_color
            , pivoted.attr_size
            , pivoted.attr_length_m
            , pivoted.attr_power_hp
            , pivoted.attr_voltage_v
            , pivoted.attr_weight_kg
            , pivoted.attr_waterproof
            , pivoted.attr_material

        from product_variants

        left join enriched_products
            on product_variants.product_id = enriched_products.product_id

        left join pivoted
            on product_variants.product_variant_id = pivoted.product_variant_id
    )

select *
from cleaned_variant_products
