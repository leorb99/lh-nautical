with
    products as (
        select *
        from {{ ref('stg_products') }}
    )

    , brands as (
        select *
        from {{ ref('stg_brands') }}
    )

    -- Self-join to flattening the category atribute
    , categories as (
        select
            child.category_id

            -- Trims and collapses internal whitespace only: initcap()-based
            -- capitalization macros were tried and rejected here, they strip
            -- hyphens (breaks "Salva-Vidas") and mangle words after them.
            -- The one known bad value ('SEGURANÇA', all caps, unlike every
            -- other category) is corrected explicitly instead, since no
            -- general-purpose casing fix proved safe against real data.
            , case
                when {{ trim_collapse_whitespace('child.category_name') }} = 'SEGURANÇA'
                    then 'Segurança'
                else {{ trim_collapse_whitespace('child.category_name') }}
            end as category_name

            , child.category_url_slug
            , case when child.created_at > current_timestamp() then null else child.created_at end as category_created_at
            , case when child.updated_at > current_timestamp() then null else child.updated_at end as category_updated_at
            , parent.category_id as parent_category_id

            , case
                when {{ trim_collapse_whitespace('parent.category_name') }} = 'SEGURANÇA'
                    then 'Segurança'
                else {{ trim_collapse_whitespace('parent.category_name') }}
            end as parent_category_name

            , child.is_active as is_category_active
        from {{ ref('stg_categories') }} as child
        left join {{ ref('stg_categories') }} as parent
            on child.parent_category_id = parent.category_id
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
            , case when products.created_at > current_timestamp() then null else products.created_at end as product_created_at
            , case when products.updated_at > current_timestamp() then null else products.updated_at end as product_updated_at
        from products
    )

    , enriched_products as (
        select
            cleaned_products.product_id
            , cleaned_products.product_name
            , cleaned_products.product_description
            , cleaned_products.product_ncm_code
            , cleaned_products.product_unit_of_measure
            , cleaned_products.is_product_active
            , cleaned_products.product_created_at
            , cleaned_products.product_updated_at
            , brands.brand_id

            -- Trim + whitespace collapse only, same reasoning as category_name
            -- above: initcap()-based capitalization was tried and rejected,
            -- it would turn "BR Náutica" into "Br Náutica" and "MaxSea" into
            -- "Maxsea", destroying intentional capitalization in real brand
            -- names. No known bad value here to correct explicitly, unlike
            -- category's 'SEGURANÇA'.
            , {{ trim_collapse_whitespace('brands.brand_name') }} as brand_name

            , brands.brand_country
            , brands.is_active as is_brand_active
            , case when brands.created_at > current_timestamp() then null else brands.created_at end as brand_created_at
            , case when brands.updated_at > current_timestamp() then null else brands.updated_at end as brand_updated_at
            , categories.category_id
            , categories.category_name
            , categories.category_url_slug
            , categories.parent_category_id
            , categories.parent_category_name
            , categories.is_category_active
            , categories.category_created_at
            , categories.category_updated_at
        from cleaned_products
        left join brands
            on cleaned_products.brand_id = brands.brand_id
        left join categories
            on cleaned_products.category_id = categories.category_id
    )

select *
from enriched_products
