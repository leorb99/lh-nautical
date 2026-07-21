with holidays as (
    select
        cast(holiday_date as date) as holiday_date
        , cast(holiday as string) as holiday_name
    from {{ ref('holidays') }}
)

, date_list as (
    select
        -- Generates a native date series
        explode(sequence(to_date('2020-01-01'), to_date('2026-12-31'), interval 1 day)) as date_day
)

, final as (
    select
        -- Keys
        cast(date_list.date_day as date) as date
        , year(date_list.date_day) * 10000 + month(date_list.date_day) * 100 + day(date_list.date_day) as date_id
        
        -- Calendar attributes
        , year(date_list.date_day) as year_num
        , quarter(date_list.date_day) as quarter_num
        , concat('Q', quarter(date_list.date_day)) as quarter_name
        , month(date_list.date_day) as month_num
        , date_format(date_list.date_day, 'MMMM') as month_name
        , date_format(date_list.date_day, 'MMM') as month_short_name
        , day(date_list.date_day) as day_month_num
        
        -- Day of the week (in databrickss: 1=Sunday, 7=Saturday)
        , dayofweek(date_list.date_day) as day_week_num
        , case 
            when dayofweek(date_list.date_day) in (1, 7) then true 
            else false 
        end as is_weekend_day
        , case dayofweek(date_list.date_day)
            when 1 then 'Sunday'
            when 2 then 'Monday'
            when 3 then 'Tuesday'
            when 4 then 'Wednesday'
            when 5 then 'Thursday'
            when 6 then 'Friday'
            when 7 then 'Saturday'
        end as day_week_name
        , case dayofweek(date_list.date_day)
            when 1 then 'Sun'
            when 2 then 'Mon'
            when 3 then 'Tue'
            when 4 then 'Wed'
            when 5 then 'Thu'
            when 6 then 'Fri'
            when 7 then 'Sat'
        end as day_week_short_name
        
        -- Holiday attributes (from left join)
        , case 
            when holidays.holiday_date is not null then true 
            else false 
        end as is_holiday
        , holidays.holiday_name
        
        -- We're considering it is a business day if it is not a weekend AND not a holiday
        , case 
            when dayofweek(date_list.date_day) not in (1, 7) and holidays.holiday_date is null then true 
            else false 
        end as is_business_day

    from date_list
    left join holidays
        on cast(date_list.date_day as date) = holidays.holiday_date
)

select * 
from final
order by date desc