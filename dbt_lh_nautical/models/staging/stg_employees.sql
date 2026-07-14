with 
    employees as (
        select
        cast(id as int) as employee_id
        , cast(full_name as string) as employee_full_name
        , cast(cpf as string) as employee_cpf
        , cast(email as string) as employee_email
        , cast(role as string) as employee_role
        , cast(primary_location_id as int) as employee_primary_location_id
        , cast(hire_date as date) as employee_hire_date
        , cast(termination_date as date) as employee_termination_date
        , cast(is_active as boolean) as is_active
        , cast(created_at as timestamp) as created_at
        , cast(updated_at as timestamp) as updated_at
        , cast(_hash as string) as _hash
        , cast(_ingested_at as timestamp) as _ingested_at
        , cast(_action as string) as _action
    from {{ source('bronze', 'employees') }}
    )

select *
from employees