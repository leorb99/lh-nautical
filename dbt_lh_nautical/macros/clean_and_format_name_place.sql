{% macro clean_and_format_name_place(column) %}
    -- Capitalize the first letter of the name
    -- Remove extra spaces
    -- Keep "de/do/da/dos/das" in lowercase
    regexp_replace(
        regexp_replace(
            regexp_replace(
                regexp_replace(
                    regexp_replace(
                        initcap(
                            regexp_replace(trim({{ column }}), '\\s+', ' ')
                        ),
                    '\\bDe\\b', 'de'),
                '\\bDo\\b', 'do'),
            '\\bDa\\b', 'da'),
        '\\bDos\\b', 'dos'),
    '\\bDas\\b', 'das')
    
{% endmacro %}

