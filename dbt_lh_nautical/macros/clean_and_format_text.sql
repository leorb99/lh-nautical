{% macro clean_and_format_text(column) %}
    -- Capitalize the first letter of the name
    -- Remove extra spaces
    -- Keep "De/Do/Da" in lowercase
    regexp_replace(
        -- Executes INITCAP and removes multiple spaces from the middle all at once
        initcap(regexp_replace(trim({{ column }}), '\\s+', ' ')),
        -- Changes "De", "Do", "Da", etc., to lowercase
        '\\b(De|Do|Da|Dos|Das)\\b',
        lower('$1')
    )

{% endmacro %}