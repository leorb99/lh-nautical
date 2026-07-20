with
    products as (
        select * from {{ ref('stg_products') }}
    )

    , brands as (
        select * from {{ ref('stg_brands') }}
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

    , enriched as (
        select
            products.product_id
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
            , brands.brand_id
            , brands.brand_name
            , categories.category_id
            , categories.category_name
            , categories.category_url_slug
            , categories.parent_category_id
            , categories.parent_category_name

        from products
        left join brands
            on products.brand_id = brands.brand_id
        left join categories
            on products.category_id = categories.category_id
    )

select *
from enriched
