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
            , child.category_name
            , child.category_url_slug
            , child.created_at
            , child.updated_at
            , parent.category_id as parent_category_id
            , parent.category_name as parent_category_name
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
            , products.created_at as product_created_at
            , products.updated_at as product_updated_at
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
            , cleaned_products.created_at as product_created_at
            , cleaned_products.updated_at as product_updated_at
            , brands.brand_id
            , brands.brand_name
            , brands.brand_country
            , brands.is_active as is_brand_active
            , brands.created_at as brand_created_at
            , brands.updated_at as brand_updated_at
            , categories.category_id
            , categories.category_name
            , categories.category_url_slug
            , categories.parent_category_id
            , categories.parent_category_name
            , categories.is_active as is_category_active
            , categories.created_at as category_created_at
            , categories.updated_at as category_updated_at
        from cleaned_products
        left join brands
            on cleaned_products.brand_id = brands.brand_id
        left join categories
            on cleaned_products.category_id = categories.category_id
    )

select *
from enriched_products
