with
    attributes as (
        select * from {{ ref('stg_attributes') }}
    )

    , variant_values as (
        select * from {{ ref('stg_variant_attribute_values') }}
    )

    , valid_colors as (
        select * from {{ ref('seed_valid_colors') }}
    )

    , cleaned_raw_values as (
        select
            product_variant_id
            , attribute_id
            , {{ nullify_garbage('value') }} as raw_value
        from variant_values
    )

    , joined as (
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
        from joined
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
            , valid_colors.valid_color
            , levenshtein(normalized_colors.normalized_raw_value, valid_colors.valid_color) as distance
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
            joined.product_variant_id
            , joined.attribute_name
            , joined.attribute_data_type
            , joined.raw_value
            , fuzzy_matched_colors.best_match_color
            , fuzzy_matched_colors.distance
        from joined
        left join fuzzy_matched_colors
            on
                joined.product_variant_id = fuzzy_matched_colors.product_variant_id
                and joined.attribute_name = fuzzy_matched_colors.attribute_name
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

select *
from pivoted
