{% macro clean_and_format_text(column) %}
    -- Capitalize the first letter of the name
    -- Remove extra spaces
    -- Keep "De/Do/Da" in lowercase
    -- Remove titles

    -- Enforce lowercase prepositions (De/Do/Da/Dos/Das)
    regexp_replace(
        -- Capitalize first letters and collapse extra spaces
        initcap(
            regexp_replace(
                -- Remove titles (Sr, Sra, Dr, etc.), dots, and hyphens, then trim
                trim(regexp_replace({{ coluna }}, '(?i)\\b(sr|sra|dr|dra)\\b|[.-]', '')), '\\s+', ' '
            )
        ),
        '\\b(De|Do|Da|Dos|Das)\\b',
        lower('$1')
    ),
        
{% endmacro %}