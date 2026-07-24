with
    int_employees as (
        select *
        from {{ ref('int_employees') }} 
    )

    , add_surrogate_key as (
        select
            -- Surrogate Key based on the Natural Key (employee_id)
            {{ dbt_utils.generate_surrogate_key(['employee_id']) }} as employee_sk
            -- Natural / Business Key
            , employee_id
            
            -- Employee Attributes
            , employee_full_name
            , employee_cpf
            , employee_email
            , employee_role
            
            -- Dates & Status Flags
            , employee_hire_date
            , employee_termination_date
            , is_active
            
            -- Audit Metadata
            , created_at
            , updated_at
            
        from int_employees
    )

select * 
from add_surrogate_key