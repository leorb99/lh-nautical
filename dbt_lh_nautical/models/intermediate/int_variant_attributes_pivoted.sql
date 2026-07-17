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

    , joined as (
        select
            v.product_variant_id
            , a.attribute_name as attribute_name
            , a.attribute_data_type
            , {{ nullify_garbage('v.value') }} as raw_value
        from variant_values v
        left join attributes a on v.attribute_id = a.attribute_id
    )

    , fuzzy_matched_colors as (
        select 
            j.product_variant_id
            , j.attribute_name
            , j.raw_value
            -- Select the valid color with the smallest orthographic distance to the dirty data using levenshtein function.
            , min_by(c.valid_colors, levenshtein(trim(lower(j.raw_value)), c.valid_colors)) as best_match_color
            , min(levenshtein(trim(lower(j.raw_value)), c.valid_colors)) as distance
        from joined j
        cross join valid_colors c
        where j.attribute_name = 'color'
        group by 1, 2, 3
    )

    , cleaned_values as (
        select
            j.product_variant_id
            , j.attribute_name
            , j.attribute_data_type
            , case 
                when j.attribute_data_type = 'text' then 
                    case 
                        when j.attribute_name = 'color' then 
                            -- If the difference is up to 2 characters, accept the correction.
                            -- If it is greater, set it to NULL.
                            case 
                                when f.distance <= 2 then f.best_match_color 
                                else null 
                            end
                            
                        when j.attribute_name = 'size' then
                            case 
                                when trim(lower(j.raw_value)) = 'gx' then 'xg'
                                else trim(lower(j.raw_value))
                            end
                        else trim(lower(j.raw_value))
                    end

                when j.attribute_data_type = 'number' then 
                    cast(
                        try_cast(
                            regexp_replace(j.raw_value, '[^0-9.]', '')
                        as decimal(10,2)) 
                    as string)

                when j.attribute_data_type = 'boolean' then
                    case 
                        when trim(lower(j.raw_value)) in ('true', 'truue', '1', 't', 'sim', 's', 'y', 'yes') then 'true'
                        when trim(lower(j.raw_value)) in ('false', 'fase', '0', 'f', 'nao', 'n', 'no') then 'false'
                        else null
                    end

                else j.raw_value
            end as attribute_value

        from joined j
        left join fuzzy_matched_colors f 
        on j.product_variant_id = f.product_variant_id 
        and j.attribute_name = f.attribute_name
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