with
    employees as (
        select *
        from {{ ref('stg_employees')}}
    )

    , cleaned_employees as (
        select 
            employee_id
            -- Format name: capitalize first letters, remove extra spaces, enforce lowercase prepositions (De/Do/Da), end remove 
            , {{ clean_and_format_name('employee_full_name') }} as employee_full_name
            , regexp_replace(employee_cpf, '[^a-zA-Z0-9]', '') as employee_cpf
            , {{ lower_trim('employee_email') }} as employee_email
            , {{ initcap_trim('employee_role') }} as employee_role
            , employee_hire_date
            , employee_termination_date
            , {{ nullify_future_date(created_at) }} as created_at
            , {{ nullify_future_date(update_at)}} as updated_at
            , is_active
        from employees
    )

select * from cleaned_employees