with
    int_employees as (
        select *
        from {{ ref('int_employees') }} 
    )

    , add_surrogate_key as (
        select
            -- Surrogate Key based on the Natural Key (employee_id)
            {{ dbt_utils.generate_surrogate_key(['employee_id']) }} as employee_sk
            , *
        from int_employees
    )

select * 
from add_surrogate_key