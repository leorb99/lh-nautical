{% macro clean_and_format_name(column) %}
    -- Capitalize the first letter of the name
    -- Remove extra spaces
    -- Keep "De/Do/Da" in lowercase
    -- Remove titles 

    -- Enforce lowercase prepositions (De/Do/Da/Dos/Das)
    regexp_replace(
        regexp_replace(
            regexp_replace(
                regexp_replace(
                    regexp_replace(
                        initcap(
                            regexp_replace(
                                trim(
                                    regexp_replace({{ column }}, '(?i)\\b(Sr|Sra|Dr|Dra)\\b|[.-]', '')
                                ),
                            '\\s+', ' ')
                            ),
                    '\\bDe\\b', 'de'),
                '\\bDo\\b', 'do'),
            '\\bDa\\b', 'da'),
        '\\bDos\\b', 'dos'),
    '\\bDas\\b', 'das')
        
{% endmacro %}